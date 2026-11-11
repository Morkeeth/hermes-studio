"""Local checks and take selection. No model downloads or publishing."""
import argparse
import hashlib
import html
from html.parser import HTMLParser
import ipaddress
import json
import math
from pathlib import Path
import re
import shutil
import socket
import subprocess
import tempfile
import urllib.request
import urllib.parse

ROOT = Path(__file__).resolve().parent

def run(argv, timeout=300):
    p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    if p.returncode:
        raise RuntimeError(p.stderr[-2000:])
    return p.stdout, p.stderr

def digest(path):
    h=hashlib.sha256()
    with open(path,'rb') as f:
        for b in iter(lambda:f.read(1024*1024),b''): h.update(b)
    return h.hexdigest()

def studio_gate(video, review=None):
    """Signal checks plus explicit, hash-bound visual review for baked-in content.

    Review is evidence supplied by a viewer, not automated visual certification.
    """
    p=Path(video).expanduser().resolve(strict=True)
    info=json.loads(run(['ffprobe','-v','error','-show_streams','-show_format','-of','json',str(p)])[0])
    v=next(s for s in info['streams'] if s['codec_type']=='video')
    duration=float(info['format']['duration'])
    sar=v.get('sample_aspect_ratio','1:1').split(':')
    aspect=int(v['width'])/int(v['height'])*(float(sar[0])/float(sar[1]) if len(sar)==2 and float(sar[1]) else 1)
    rotation=next((abs(int(s.get('rotation',0)))%180 for s in v.get('side_data_list',[]) if 'rotation' in s),0)
    if rotation==90: aspect=1/aspect
    checks={'portrait_9_16':abs(aspect-9/16)<0.002,'duration_15_60':15<=duration<=60}
    # Decode original frames: scaling could hide small movement and change the freeze verdict.
    log=run(['ffmpeg','-nostdin','-hide_banner','-threads','2','-i',str(p),'-an','-vf','freezedetect=n=-60dB:d=1','-f','null','-'],600)[1]
    intervals=[]; start=None
    for line in log.splitlines():
        m=re.search(r'freeze_start: ([\d.]+)',line)
        if m:start=float(m.group(1))
        m=re.search(r'freeze_end: ([\d.]+)',line)
        if m and start is not None:
            intervals.append([start,float(m.group(1))]);start=None
    if start is not None:intervals.append([start,duration])
    checks['no_static_hold_over_1s']=not any(b-a>1 for a,b in intervals)
    loudness=None
    if any(s['codec_type']=='audio' for s in info['streams']):
        log=run(['ffmpeg','-nostdin','-hide_banner','-i',str(p),'-vn','-af','loudnorm=I=-14:print_format=json','-f','null','-'],600)[1]
        m=re.search(r'\{\s*"input_i".*?\}',log,re.S)
        if m:loudness=float(json.loads(m.group())['input_i'])
    checks['loudness_minus14_plusminus2']=loudness is not None and math.isfinite(loudness) and abs(loudness+14)<=2
    evidence=json.loads(Path(review).read_text()) if review else {}
    bound=evidence.get('sha256')==digest(p) and bool(evidence.get('reviewer')) and bool(evidence.get('method'))
    for key in ('title_on_first_frame','captions_visible','no_plain_zoom'):
        checks[key]=bound and evidence.get(key) is True
    reasons=[k.replace('_',' ') for k,ok in checks.items() if not ok]
    return {'pass':all(checks.values()),'reasons':reasons,'checks':checks,'duration':duration,'integrated_lufs':loudness,'freeze_intervals':intervals,'visual_review_bound':bound,'visual_review_method':evidence.get('method'),'sha256':digest(p)}

class TitleParser(HTMLParser):
    def __init__(self):super().__init__();self.active=False;self.parts=[];self.og=None
    def handle_starttag(self,tag,attrs):
        a=dict(attrs)
        if tag=='title':self.active=True
        if tag=='meta' and a.get('property')=='og:title':self.og=a.get('content')
    def handle_endtag(self,tag):
        if tag=='title':self.active=False
    def handle_data(self,data):
        if self.active:self.parts.append(data)

def public_url(url):
    u=urllib.parse.urlsplit(url)
    if u.scheme not in ('http','https') or not u.hostname or u.username or u.password:raise ValueError('Expected a public HTTP(S) URL')
    for addr in socket.getaddrinfo(u.hostname,u.port or (443 if u.scheme=='https' else 80),type=socket.SOCK_STREAM):
        if not ipaddress.ip_address(addr[4][0]).is_global:raise ValueError('Private/local URLs are not public posts')
    return url

class PublicRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,req,fp,code,msg,headers,newurl):
        public_url(newurl)
        return super().redirect_request(req,fp,code,msg,headers,newurl)

def studio_verify_post(url):
    """Confirms reachable HTML and title, not platform ownership or publication date."""
    try:
        public_url(url)
        opener=urllib.request.build_opener(PublicRedirect())
        with opener.open(urllib.request.Request(url,headers={'User-Agent':'HermesStudio/0.1'}),timeout=15) as response:
            final=response.geturl();public_url(final)
            status=response.status
            if status!=200:raise ValueError(f'HTTP {status}')
            if 'text/html' not in response.headers.get('Content-Type',''):raise ValueError('Expected an HTML page')
            body=response.read(2_000_001)
            if len(body)>2_000_000:raise ValueError('Page exceeds 2 MB limit')
            parser=TitleParser();parser.feed(body.decode(response.headers.get_content_charset() or 'utf-8',errors='replace'))
            title=html.unescape(parser.og or ''.join(parser.parts)).strip()
            if not title:raise ValueError('No page title returned')
            return {'exists':True,'url':final,'title':title,'status':status,'scope':'Reachable HTML with title; platform identity and soft-404 pages require human review'}
    except Exception as exc:return {'exists':False,'url':url,'title':None,'reason':str(exc)}

def rank_hooks(segments):
    ranked=[]
    for s in segments:
        text=s['text'].strip(); words=text.split()
        if len(words)<4:continue
        score=0;reasons=[]
        if '?' in text:score+=2;reasons.append('opens a question')
        if re.search(r'\b(but|instead|wrong|problem|never|failed|actually)\b',text,re.I):score+=2;reasons.append('states a tension')
        if 6<=len(words)<=28:score+=1;reasons.append('compact spoken setup')
        if re.match(r'^(um|uh|so|and)\b',text,re.I):score-=1
        ranked.append({**s,'score':score,'reason':'; '.join(reasons) or 'clear spoken phrase; lower hook signal'})
    return sorted(ranked,key=lambda s:(-s['score'],s['start']))

def transcribe_folder(folder, model, output):
    folder=Path(folder).expanduser().resolve(strict=True);model=Path(model).expanduser().resolve(strict=True)
    output=Path(output).expanduser().resolve();output.mkdir(parents=True,exist_ok=False)
    backend='whisper.cpp'
    try:
        from faster_whisper import WhisperModel
        backend='faster-whisper'
        engine=WhisperModel(str(model),device='cpu',compute_type='int8',local_files_only=True)
    except ImportError:
        if not shutil.which('whisper-cli'):raise RuntimeError('No local faster-whisper or whisper-cli installed')
    results=[]
    for idx,p in enumerate(sorted(folder.iterdir())):
        if p.suffix.lower() not in ('.mp4','.mov','.mkv','.wav','.m4a'):continue
        wav=output/f'{idx:03d}-{p.stem}.wav'
        run(['ffmpeg','-nostdin','-n','-v','error','-threads','1','-i',str(p),'-vn','-ar','16000','-ac','1','-threads','1',str(wav)])
        if backend=='faster-whisper':
            segs,_=engine.transcribe(str(wav),beam_size=1,word_timestamps=True)
            segments=[{'start':s.start,'end':s.end,'text':s.text,
                       'words':[{'start':w.start,'end':w.end,'text':w.word} for w in (s.words or [])]} for s in segs]
        else:
            prefix=output/f'{idx:03d}-{p.stem}'
            run(['whisper-cli','-m',str(model),'-f',str(wav),'-l','en','-ojf','-sow','-ml','32','-of',str(prefix),'-t','1'],900)
            data=json.loads(Path(str(prefix)+'.json').read_text())
            segments=[{'start':s['offsets']['from']/1000,'end':s['offsets']['to']/1000,'text':s['text'],
                       'words':whisper_words(s.get('tokens',[]))} for s in data['transcription']]
        for segment in segments:
            normalize=lambda value:re.sub(r'[^a-z0-9]+','',value.casefold())
            words=segment.get('words',[])
            complete=normalize(segment['text'])==normalize(' '.join(w['text'] for w in words))
            segment['word_timing_complete']=bool(words) and complete and not any(w['end']-w['start']>1.5 for w in words)
            segment['timing_note']='ASR estimates require listening; missing word times or stretched words are not repaired automatically.'
        results.extend({**s,'file':str(p)} for s in segments)
    if not results:raise ValueError('No speech segments found in supported clips')
    report={'backend':backend,'model':str(model),'segments':results,'hooks':rank_hooks(results),'ranking':'Deterministic language heuristic, not a semantic or taste verdict'}
    (output/'takes.json').write_text(json.dumps(report,indent=2));return report

def whisper_words(tokens):
    """Join Whisper subword tokens using their ASR offsets; these are estimates."""
    words=[]
    for token in tokens:
        if token.get('id',50000)>=50000:continue
        text=token.get('text','');a=token['offsets']['from']/1000;b=token['offsets']['to']/1000
        if b<=a or a<0:continue
        if not words or text.startswith(' '):words.append({'start':a,'end':b,'text':text.strip()})
        else:words[-1]['text']+=text;words[-1]['end']=max(b,words[-1]['end'])
    return words

def main():
    parser=argparse.ArgumentParser();sub=parser.add_subparsers(dest='command',required=True)
    gate=sub.add_parser('gate');gate.add_argument('video');gate.add_argument('--review')
    post=sub.add_parser('verify-post');post.add_argument('url')
    take=sub.add_parser('take');take.add_argument('folder');take.add_argument('--model',required=True);take.add_argument('--output',required=True)
    args=parser.parse_args()
    try:
        if args.command=='gate':result=studio_gate(args.video,args.review)
        elif args.command=='verify-post':result=studio_verify_post(args.url)
        else:result=transcribe_folder(args.folder,args.model,args.output)
        print(json.dumps(result,indent=2));return 1 if result.get('pass') is False or result.get('exists') is False else 0
    except Exception as exc:print(json.dumps({'error':str(exc)}));return 2

if __name__=='__main__':raise SystemExit(main())

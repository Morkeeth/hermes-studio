"""Compose existing editor tools through Hermes's public dispatch surface."""
import json
import math
from pathlib import Path


def seconds(value):
    n=float(value)
    if not math.isfinite(n):raise ValueError('Timestamp must be finite')
    return n


def srt_time(value):
    ms=round(value*1000);h,ms=divmod(ms,3600000);m,ms=divmod(ms,60000);s,ms=divmod(ms,1000)
    return f'{h:02}:{m:02}:{s:02},{ms:03}'


def prepare_cut(source, start, end, title, captions, output_dir, crop_box=None):
    """Validate all inputs before invoking any render. Caption times use source time."""
    source=Path(source).expanduser().resolve(strict=True)
    start,end=seconds(start),seconds(end)
    if start<0 or not 15<=end-start<=60:raise ValueError('Choose a 15–60 second source interval')
    if not isinstance(title,str) or not title.strip() or len(title)>100:raise ValueError('Supply a concise first-frame title (max 100 characters)')
    if not isinstance(captions,list) or not captions:raise ValueError('Supply locally aligned caption words or phrases')
    lines=[];previous=0
    for item in captions:
        a=max(start,seconds(item['start']));b=min(end,seconds(item['end']))
        text=' '.join(str(item['text']).split())
        if b<=a or not text:continue
        if a-start<previous-0.001:raise ValueError('Captions must be ordered and non-overlapping')
        previous=b-start
        lines.append(f'{len(lines)+1}\n{srt_time(a-start)} --> {srt_time(b-start)}\n{text}\n')
    if not lines:raise ValueError('No captions overlap the chosen interval')
    if crop_box is not None:
        if not isinstance(crop_box,list) or len(crop_box)!=4 or any(type(n) is not int or n<0 for n in crop_box) or min(crop_box[2:])<2:
            raise ValueError('crop_box must be nonnegative integer [x,y,width,height]')
    output=Path(output_dir).expanduser().resolve()
    output.mkdir(parents=True,exist_ok=False)
    srt=output/'captions.srt';srt.write_text('\n'.join(lines))
    steps=[
      ('lk_trim',{'input':str(source),'start':start,'end':end,'mode':'accurate','output':str(output/'01-trim.mp4')}),
      ('lk_crop_to_aspect',{'aspect':'9:16','anchor':'center','output_width':720,'output':str(output/'02-portrait.mp4')}),
      ('lk_burn_captions',{'captions':str(srt),'font_size':36,'outline_px':0,'bottom_margin_px':540,'reels_safe':True,'color':'white','output':str(output/'03-captions.mp4')}),
      ('lk_add_text',{'text':title.strip(),'position':'top','font_size':36,'margin_px':90,'outline_px':0,'box':False,'start':0,'end':min(3,end-start),'output':str(output/'04-title.mp4')}),
      ('lk_normalize_loudness',{'target_lufs':-14,'verify':True,'output':str(output/'short.mp4')})]
    if crop_box is not None:
        x,y,width,height=crop_box
        steps.insert(1,('lk_crop',{'x':x,'y':y,'width':width,'height':height,'output':str(output/'01b-fixed-crop.mp4')}))
    plan={'source':str(source),'start':start,'end':end,'title':title,'captions':str(srt),'steps':[{'tool':name,'args':args} for name,args in steps],
          'visual_checks':'Title visibility, caption accuracy/placement, crop and absence of plain zoom require actual visual review; no detector is claimed.'}
    (output/'plan.json').write_text(json.dumps(plan,indent=2));return plan


def execute_cut(plan, dispatch):
    """Call installed editor handlers in order; stop on any failure. No repair loops."""
    output=Path(plan['captions']).parent
    events=[];previous=None
    try:
        for step in plan['steps']:
            args=dict(step['args'])
            if previous is not None:args['input']=previous
            raw=dispatch(step['tool'],args)
            result=json.loads(raw) if isinstance(raw,str) else raw
            events.append({'tool':step['tool'],'args':args,'result':result})
            if not isinstance(result,dict) or result.get('ok') is not True or not result.get('output'):
                raise RuntimeError(f"{step['tool']} failed: {result}")
            actual=Path(result['output']).resolve(strict=True)
            actual.relative_to(output.resolve());previous=str(actual)
        return {'ok':True,'output':previous,'calls':events,'review_required':True}
    finally:
        (output/'editor-calls.json').write_text(json.dumps(events,indent=2))

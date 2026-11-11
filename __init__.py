"""Hermes Studio plugin registration; the checks also run standalone."""
import json
from pathlib import Path
from .studio import studio_gate, studio_verify_post, transcribe_folder
from .workflow import prepare_cut, execute_cut
from .library import discover
from .session import build
from .channel import request as channel_request, status as channel_status

def gate_handler(args, **kwargs):
    try:return json.dumps(studio_gate(args['video'],args.get('review')))
    except Exception as exc:return json.dumps({'pass':False,'reasons':[str(exc)]})

def post_handler(args, **kwargs):
    return json.dumps(studio_verify_post(args['url']))

def register(ctx):
    ctx.register_skill('take-to-short', Path(__file__).with_name('SKILL.md'),
                       description='Local camera takes to a checked vertical short')
    def channel_handler(args, **kwargs):
        try:return json.dumps(channel_request(args))
        except Exception as exc:return json.dumps({'ok':False,'error':str(exc)})

    def status_handler(args, **kwargs):
        try:return json.dumps(channel_status(args['job']))
        except Exception as exc:return json.dumps({'ok':False,'error':str(exc)})

    for name,handler,description,properties,required in [
      ('studio_request',channel_handler,'Queue a conversation request for the configured local workstation. No render or delivery is implied by queueing. Use prepare with an actual downloaded attachment, library, preview, or an explicit music-level edit on a parent project.',
       {'action':{'type':'string','enum':['library','prepare','edit','preview']},'request_key':{'type':'string'},'source':{'type':'string'},'query':{'type':'string'},'parent':{'type':'string'},'project_id':{'type':'string'},'edit':{'type':'object','properties':{'music_gain_db':{'type':'number'}},'additionalProperties':False}},['action','request_key']),
      ('studio_status',status_handler,'Read actual queued job state. queued and awaiting_render_gate are not completed exports.',{'job':{'type':'string'}},['job'])]:
        ctx.register_tool(name=name,toolset='hermes_studio',schema={'name':name,'description':description,'parameters':{'type':'object','properties':properties,'required':required,'additionalProperties':False}},handler=handler)
    for name,handler,description,properties,required in [
        ('studio_gate',gate_handler,'Check a finished short with FFmpeg and hash-bound visual review.',{'video':{'type':'string'},'review':{'type':'string'}},['video']),
        ('studio_verify_post',post_handler,'Read a public HTML URL and return its title. Does not publish.',{'url':{'type':'string'}},['url'])]:
        ctx.register_tool(name=name,toolset='hermes_studio',schema={'name':name,'description':description,'parameters':{'type':'object','properties':properties,'required':required,'additionalProperties':False}},handler=handler)
    def take_handler(args, **kwargs):
        try:return json.dumps(transcribe_folder(args['folder'],args['model'],args['output']))
        except Exception as exc:return json.dumps({'ok':False,'error':str(exc)})

    def library_handler(args, **kwargs):
        try:return json.dumps(discover(args['roots'], args.get('query',''), args.get('kind'), args.get('limit',20)))
        except Exception as exc:return json.dumps({'ok':False,'error':str(exc)})

    def build_handler(args, **kwargs):
        try:return json.dumps(build(args['recipe'], args['output'], args.get('render',False)))
        except Exception as exc:return json.dumps({'ok':False,'error':str(exc)})

    for name,handler,description,properties,required in [
      ('studio_library',library_handler,'Search real local media-library filenames. No semantic vision or inferred license/approval.',
       {'roots':{'type':'array','items':{'type':'string'}},'query':{'type':'string'},'kind':{'type':'string'},'limit':{'type':'integer'}},['roots']),
      ('studio_build',build_handler,'Run one explicit local recipe: fresh transcription, library selection, editable word-entry animation and optional gated native export. No paid generation or publishing.',
       {'recipe':{'type':'string'},'output':{'type':'string'},'render':{'type':'boolean','default':False}},['recipe','output'])]:
        ctx.register_tool(name=name,toolset='hermes_studio',schema={'name':name,'description':description,'parameters':{'type':'object','properties':properties,'required':required,'additionalProperties':False}},handler=handler)

    def cut_handler(args, **kwargs):
        try:
            plan=prepare_cut(args['source'],args['start'],args['end'],args['title'],args['captions'],args['output_dir'],args.get('crop_box'))
            if not args.get('render',False):return json.dumps({'ok':True,'rendered':False,'plan':plan})
            result=execute_cut(plan,ctx.dispatch_tool)
            result['gate']=studio_gate(result['output'],args.get('review'))
            return json.dumps(result)
        except Exception as exc:return json.dumps({'ok':False,'error':str(exc)})

    for name,handler,description,properties,required in [
      ('studio_take',take_handler,'Transcribe a local card folder and rank hook phrases. No downloads or paid APIs.',
       {key:{'type':'string'} for key in ['folder','model','output']},['folder','model','output']),
      ('studio_cut',cut_handler,'Plan or explicitly render a selected 15–60 second short through installed hermes-video-editor tools. Writes new local files; visual review required.',
       {'source':{'type':'string'},'start':{'type':'number'},'end':{'type':'number'},'title':{'type':'string'},
        'captions':{'type':'array','items':{'type':'object','properties':{'start':{'type':'number'},'end':{'type':'number'},'text':{'type':'string'}},'required':['start','end','text']}},
        'output_dir':{'type':'string'},'crop_box':{'type':'array','items':{'type':'integer'},'minItems':4,'maxItems':4},'render':{'type':'boolean','default':False},'review':{'type':'string'}},
       ['source','start','end','title','captions','output_dir'])]:
        ctx.register_tool(name=name,toolset='hermes_studio',schema={'name':name,'description':description,'parameters':{'type':'object','properties':properties,'required':required,'additionalProperties':False}},handler=handler)

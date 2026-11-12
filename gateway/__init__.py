"""Small gateway companion; full editing stays on the supported workstation."""
import json
from pathlib import Path
from .channel import request, status, EDIT_SCHEMA

def register(ctx):
    ctx.register_skill('channel-flow',Path(__file__).with_name('SKILL.md'),description='Use the configured local Studio worker from this conversation')
    def submit(args,**kwargs):
        try:return json.dumps(request(args))
        except Exception as exc:return json.dumps({'ok':False,'error':str(exc)})
    def read(args,**kwargs):
        try:return json.dumps(status(args.get('job'),reply_to_message_id=args.get('reply_to_message_id'),source=args.get('source')))
        except Exception as exc:return json.dumps({'ok':False,'error':str(exc)})
    props={'action':{'type':'string','enum':['library','prepare','edit','preview','alternatives','finish','generate','approve','revise']},'style':{'type':'string'},'title':{'type':'string'},'request_key':{'type':'string'},'source':{'type':'string'},'query':{'type':'string'},'parent':{'type':'string'},'reply_to_message_id':{'type':'integer'},'project_id':{'type':'string'},'edit':EDIT_SCHEMA}
    for name,handler,properties,required in [('studio_request',submit,props,['action','request_key']),('studio_status',read,{'job':{'type':'string'},'reply_to_message_id':{'type':'integer'},'source':{'type':'string'}},[])]:
        ctx.register_tool(name=name,toolset='hermes_studio_bridge',schema={'name':name,'description':'Queue or inspect an actual Studio workstation request. Queueing is not an export or delivery. Read the channel-flow skill first.','parameters':{'type':'object','properties':properties,'required':required,'additionalProperties':False}},handler=handler)

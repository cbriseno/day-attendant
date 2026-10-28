#!/usr/bin/env python3
"""Real browser layout checks via the installed isolated Chrome AXI session."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

p=argparse.ArgumentParser();p.add_argument('--session',required=True);p.add_argument('--browser-url',required=True);p.add_argument('--out',required=True);args=p.parse_args()
env=dict(os.environ,CHROME_DEVTOOLS_AXI_SESSION=args.session,CHROME_DEVTOOLS_AXI_BROWSER_URL=args.browser_url)
def axi(*command,input=None):
    result=subprocess.run(['chrome-devtools-axi',*command],input=input,env=env,capture_output=True,text=True,timeout=45)
    if result.returncode:raise RuntimeError(result.stdout+result.stderr)
    return result.stdout
report={'scope':'Actual browser responsive geometry, modal controls, light/dark and collapsible chat; no model calls','variants':[]}
try:
    axi('eval','location.reload()')
    for width,height in [(1440,1000),(1080,800),(768,1024),(390,844),(320,640)]:
        axi('resize',str(width),str(height))
        script='''await page.wait('#settings-open');await page.click('#settings-open');console.log(JSON.stringify(await page.eval(()=>{const d=document.querySelector('#settings-dialog'),r=d.getBoundingClientRect(),f=d.querySelector('.modal-footer').getBoundingClientRect();return {width:innerWidth,height:innerHeight,documentFits:document.documentElement.scrollWidth<=innerWidth,dialogFits:r.left>=0&&r.right<=innerWidth&&r.bottom<=innerHeight,footerVisible:f.bottom<=innerHeight,settingsReadable:d.querySelector('.modal-body').clientHeight>80}})));await page.press('Escape');'''
        output=axi('run',input=script)
        values=json.loads(output.strip());report['variants'].append(values)
        assert all(values[k] for k in ('documentFits','dialogFits','footerVisible','settingsReadable')),values
    axi('resize','1440','1000')
    report['passed']=True
except BaseException as error:report['passed']=False;report['error']=str(error);raise
finally:Path(args.out).write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report))

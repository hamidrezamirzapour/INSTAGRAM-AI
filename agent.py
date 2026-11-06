"""Headline-only Google Trends to Instagram Reels pipeline."""
import os, re, json, time, hashlib, logging, subprocess
from datetime import datetime, timezone, timedelta
from pathlib import Path
import xml.etree.ElementTree as ET
import requests
from PIL import Image, ImageDraw, ImageFont

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
BASE=Path(__file__).resolve().parent
OUT=BASE/'docs'/'media'; WORK=BASE/'work'
REGIONS=('US','GB','CA','AU')
RSS='https://trends.google.com/trending/rss?geo={}'
MODEL=os.getenv('GEMINI_MODEL','gemini-3.1-flash-lite')
ALLOW={'ai','artificial intelligence','technology','tech','robot','robotics','chip','semiconductor','software','smartphone','computer','cybersecurity','machine learning','language model','openai','google','microsoft','apple','nvidia','meta','amazon','anthropic','data center','innovation'}
DENY={'politic','election','war','attack','death','murder','shoot','violence','traged','disaster','medicine','medical','health','drug','hospital','finance','stock','crypto','invest','bank','lawsuit','court','scam'}
STOP={'the','and','for','with','from','this','that','into','over','after','about','new','how','what','why','when','your','their','its','are','was','has','have','will','can','could','today','news'}
NS={'ht':'https://trends.google.com/trending/rss'}

def now(): return datetime.now(timezone.utc)
def parse_date(s):
    if not isinstance(s, str) or not s.strip(): return None
    value=s.strip()
    try:
        d=datetime.fromisoformat(value.replace('Z','+00:00'))
        return d.replace(tzinfo=timezone.utc) if d.tzinfo is None else d.astimezone(timezone.utc)
    except ValueError:
        pass
    from email.utils import parsedate_to_datetime
    try:
        d=parsedate_to_datetime(value)
        return d.replace(tzinfo=timezone.utc) if d.tzinfo is None else d.astimezone(timezone.utc)
    except (TypeError, ValueError, OverflowError): return None
def words(s): return set(re.findall(r'[a-z0-9]+',s.lower()))-STOP
def related(a,b):
    x,y=words(a),words(b)
    return bool(x and y and len(x&y)/max(1,min(len(x),len(y)))>=0.34)
def safe_topic(title):
    s=title.lower()
    if any(w in s for w in DENY): return False
    return any(w in s for w in ALLOW)
def fetch_items(session=None):
    session=session or requests.Session(); rows=[]
    for region in REGIONS:
        r=session.get(RSS.format(region),timeout=(8,20)); r.raise_for_status()
        root=ET.fromstring(r.content)
        for item in root.findall('.//item'):
            title=(item.findtext('title') or '').strip(); pub=parse_date(item.findtext('pubDate') or '')
            if not title or not pub or now()-pub>timedelta(hours=48) or pub>now()+timedelta(hours=1) or not safe_topic(title): continue
            articles=[]
            for n in item.findall('ht:news_item',NS):
                at=(n.findtext('ht:news_item_title',default='',namespaces=NS) or '').strip()
                source=(n.findtext('ht:news_item_source',default='',namespaces=NS) or '').strip()
                url=(n.findtext('ht:news_item_url',default='',namespaces=NS) or '').strip()
                if at and source and url.startswith('https://'):
                    articles.append({'title':at,'source':source,'url':url})
            rows.append({'topic':title,'date':pub.isoformat(),'region':region,'articles':articles})
    return rows
def candidates(rows):
    groups=[]
    for row in sorted(rows,key=lambda x:x['date'],reverse=True):
        g=next((g for g in groups if related(row['topic'],g['topic'])),None)
        if g is None: g={'topic':row['topic'],'date':row['date'],'articles':[],'regions':set()}; groups.append(g)
        g['regions'].add(row['region']); g['articles']+=row['articles']
    out=[]
    for g in groups:
        unique={a['source'].strip().lower():a for a in g['articles'] if a.get('source') and a.get('title') and a.get('url','').startswith('https://')}
        if len(unique)>=2:
            g['articles']=list(unique.values())[:8]; g['regions']=sorted(g['regions']); out.append(g)
    return out
def ask_gemini(group,api_key,model=None):
    model=model or os.getenv('GEMINI_MODEL','gemini-3.1-flash-lite')
    url=f'https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent'
    schema={'type':'OBJECT','properties':{'hook':{'type':'STRING'},'insight':{'type':'STRING'},'context':{'type':'STRING'},'caption':{'type':'STRING'}},'required':['hook','insight','context','caption']}
    headlines=[{'source':a['source'],'headline':a['title']} for a in group['articles']]
    prompt=('Write a concise original English script for a three-card Instagram Reel using ONLY the supplied headlines. '
      'Headlines are not verified article bodies. State only what is directly supported by the wording; do not add specifics, numbers, causes, predictions, or details. '
      'If the headlines do not support a meaningful safe insight, return empty strings for all fields. Do not infer facts from topic familiarity. '
      'Avoid politics, medicine, finance, tragedy, and violence. Include no copied article prose. Topic: '+group['topic']+'; headlines: '+json.dumps(headlines,ensure_ascii=False))
    r=requests.post(url,headers={'x-goog-api-key':api_key,'Content-Type':'application/json'},json={'contents':[{'parts':[{'text':prompt}]}],'generationConfig':{'responseMimeType':'application/json','responseSchema':schema,'temperature':0.2}},timeout=(10,45)); r.raise_for_status()
    obj=json.loads(r.json()['candidates'][0]['content']['parts'][0]['text'])
    if set(obj)!={'hook','insight','context','caption'} or not all(isinstance(v,str) for v in obj.values()): raise ValueError('Gemini response failed schema validation')
    if not all(v.strip() for v in obj.values()) or any(len(v)>500 for v in obj.values()): raise ValueError('Insufficient or oversized script; skip')
    return obj
def state_read():
    p=BASE/'data'/'state.json'
    try:
        s=json.loads(p.read_text(encoding='utf-8'))
        return {'last_published':s.get('last_published'),'recent_hashes':s.get('recent_hashes',[]),'pending':s.get('pending')}
    except (OSError,json.JSONDecodeError,AttributeError): return {'last_published':None,'recent_hashes':[],'pending':None}
def state_write(s):
    p=BASE/'data'/'state.json'; p.parent.mkdir(parents=True,exist_ok=True)
    tmp=p.with_suffix('.tmp'); tmp.write_text(json.dumps(s,indent=2)+'\n',encoding='utf-8'); tmp.replace(p)
def cadence_ok(state,days,at=None):
    if days not in (1,2): raise ValueError('POST_EVERY_DAYS must be 1 or 2')
    if not state.get('last_published'): return True
    last=parse_date(state['last_published']); at=at or now()
    return bool(last and at-last>=timedelta(days=days))
def font(size,bold=False):
    paths=['/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf' if bold else '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf','/System/Library/Fonts/Supplemental/Arial.ttf']
    for p in paths:
        if Path(p).exists(): return ImageFont.truetype(p,size)
    return ImageFont.load_default()
def fit_lines(draw,text,f,maxw):
    lines=[]
    for para in text.splitlines() or ['']:
        line=''
        for word in para.split():
            test=(line+' '+word).strip()
            if draw.textbbox((0,0),test,font=f)[2]<=maxw: line=test
            else:
                if line: lines.append(line)
                line=word
        if line: lines.append(line)
    return lines
def draw_card(path,kicker,title,body,sources,date):
    im=Image.new('RGB',(1080,1920),(9,17,34)); d=ImageDraw.Draw(im)
    d.rounded_rectangle((58,68,1022,1850),radius=44,fill=(17,31,55),outline=(44,213,190),width=4)
    d.text((110,135),kicker.upper(),font=font(34,True),fill=(71,235,206)); y=320
    for txt,size,color in [(title,78,(255,255,255)),(body,49,(207,220,239))]:
        f=font(size,size==78)
        for line in fit_lines(d,txt,f,850): d.text((110,y),line,font=f,fill=color); y+=int(size*1.35)
        y+=65
    sy=1650; d.line((110,sy-35,970,sy-35),fill=(44,213,190),width=3)
    src='Sources: '+', '.join(sorted(set(sources)))
    for line in fit_lines(d,src,font(25),850)[:2]: d.text((110,sy),line,font=font(25),fill=(177,196,220)); sy+=38
    d.text((110,1780),date,font=font(25),fill=(177,196,220)); im.save(path,'JPEG',quality=91,optimize=True)
def create_media(script,group):
    OUT.mkdir(parents=True,exist_ok=True); WORK.mkdir(exist_ok=True)
    digest=hashlib.sha256((group['topic']+group['date']).encode()).hexdigest()[:12]
    cards=[('TREND SIGNAL',script['hook'],script['context']),('WHAT THE HEADLINES SAY',script['insight'],script['insight']),('WHY IT MATTERS',script['context'],'A cautious summary based only on the headlines shown. Review the original sources for more detail.')]
    names=[]
    for i,(label,title,body) in enumerate(cards,1):
        p=WORK/f'card{i}.jpg'; draw_card(p,label,title,body,[a['source'] for a in group['articles']],now().strftime('%d %b %Y UTC')); names.append(p)
    video=OUT/f'{digest}.mp4'
    cmd=['ffmpeg','-y','-hide_banner','-loglevel','error']
    for p in names: cmd += ['-loop','1','-t','6','-i',str(p)]
    cmd += ['-f','lavfi','-t','18','-i','anullsrc=r=48000:cl=stereo','-filter_complex','[0:v]scale=1080:1920,fps=30[v0];[1:v]scale=1080:1920,fps=30[v1];[2:v]scale=1080:1920,fps=30[v2];[v0][v1][v2]concat=n=3:v=1:a=0[v]','-map','[v]','-map','3:a','-t','18','-r','30','-c:v','libx264','-pix_fmt','yuv420p','-c:a','aac','-ar','48000','-b:a','128k','-movflags','+faststart',str(video)]
    subprocess.run(cmd,check=True,timeout=180)
    return video,digest
def redact(s):
    for name in ('PAGE_ACCESS_TOKEN','GEMINI_API_KEY'):
        val=os.getenv(name)
        if val: s=s.replace(val,'[REDACTED]')
    return s
def publish(video_url,caption):
    token=os.environ['PAGE_ACCESS_TOKEN']; ig=os.environ['IG_USER_ID']; ver=os.getenv('META_API_VERSION','v26.0'); base=f'https://graph.facebook.com/{ver}'
    sess=requests.Session(); hdr={'Authorization':'Bearer '+token}; timeout=(10,30)
    r=sess.get(video_url,headers={'Range':'bytes=0-4095'},stream=True,timeout=timeout,allow_redirects=True)
    try:
        chunk=next(r.iter_content(2048),b'')
        if r.status_code not in (200,206) or 'video/mp4' not in r.headers.get('Content-Type','').lower() or len(chunk)<1000: raise RuntimeError('Hosted MP4 check failed')
    finally: r.close()
    r=sess.post(f'{base}/{ig}/media',headers=hdr,data={'media_type':'REELS','video_url':video_url,'share_to_feed':'true','caption':caption},timeout=timeout); r.raise_for_status(); cid=r.json()['id']
    for _ in range(30):
        r=sess.get(f'{base}/{cid}',headers=hdr,params={'fields':'status_code'},timeout=timeout); r.raise_for_status(); status=r.json().get('status_code')
        if status=='FINISHED': break
        if status in ('ERROR','EXPIRED'): raise RuntimeError('Instagram media processing '+str(status))
        time.sleep(10)
    else: raise TimeoutError('Instagram container did not finish before timeout')
    r=sess.post(f'{base}/{ig}/media_publish',headers=hdr,data={'creation_id':cid},timeout=timeout); r.raise_for_status(); return r.json()['id']
def main():
    state=state_read(); days=int(os.getenv('POST_EVERY_DAYS','2'))
    if not cadence_ok(state,days): logging.info('Cadence not due; skipping.'); return
    if state.get('pending'):
        logging.warning('An earlier publication is pending/recovery-required; skipping to avoid duplicate publication.'); return
    rows=fetch_items(); groups=candidates(rows); seen=set(state.get('recent_hashes',[]))
    api_key=os.getenv('GEMINI_API_KEY')
    if not api_key: raise RuntimeError('GEMINI_API_KEY is required for generation')
    for g in groups:
        h=hashlib.sha256(g['topic'].lower().encode()).hexdigest()[:16]
        if h in seen: continue
        try: script=ask_gemini(g,api_key)
        except Exception as e: logging.warning('Candidate skipped after generation/validation: %s',redact(str(e))); continue
        video,digest=create_media(script,g)
        repo=os.getenv('GITHUB_REPOSITORY','').split('/',1)
        if len(repo)!=2: raise RuntimeError('GITHUB_REPOSITORY unavailable')
        pending={'video':str(video.relative_to(BASE)),'caption':script['caption'],'topic':g['topic'],'topic_hash':h,'container_id':None,'created_at':now().isoformat()}
        WORK.mkdir(exist_ok=True); (WORK/'pending.json').write_text(json.dumps(pending,indent=2)+'\n',encoding='utf-8')
        (WORK/'preview').mkdir(exist_ok=True); (WORK/'preview'/'script.json').write_text(json.dumps({'script':script,'topic':g['topic'],'sources':g['articles']},indent=2)+'\n',encoding='utf-8')
        if os.getenv('AUTO_PUBLISH','false').lower()=='true':
            state['pending']={k:pending[k] for k in ('video','topic_hash','created_at')}; state['pending']['status']='ready'; state_write(state)
        logging.info('Preview created: %s; headline-only sources: %d',video.name,len(g['articles']))
        logging.info('Publication, if enabled, starts only after GitHub Pages deployment and public MP4 check.'); return
    logging.info('No eligible, sufficiently sourced unused topics; clean skip.')
if __name__=='__main__':
    try: main()
    except Exception as exc:
        logging.error('%s',redact(str(exc))); raise SystemExit(1)

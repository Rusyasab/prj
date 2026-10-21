import os,json,sqlite3,secrets,hashlib,hmac,datetime,base64
from http.server import ThreadingHTTPServer,SimpleHTTPRequestHandler
from http.cookies import SimpleCookie
from decimal import Decimal,ROUND_HALF_UP
from pathlib import Path
ROOT=Path(__file__).parent
DB=os.environ.get('TRAVEL_DB',str(ROOT/'travel.sqlite3'))
def db():
 c=sqlite3.connect(DB);c.row_factory=sqlite3.Row;c.execute('PRAGMA foreign_keys=ON');return c
def now():return datetime.datetime.now(datetime.timezone.utc).isoformat()
def money(v):
 n=Decimal(str(v));assert n.is_finite() and n>0,'Сумма должна быть больше нуля';return int((n*100).quantize(Decimal('1'),rounding=ROUND_HALF_UP))
def rows(c,q,p=()):return [dict(r) for r in c.execute(q,p)]
def init():
 with db() as c:c.executescript('''
 CREATE TABLE IF NOT EXISTS users(id TEXT PRIMARY KEY,email TEXT UNIQUE,name TEXT,password TEXT,salt TEXT,notifications INTEGER DEFAULT 1);
 CREATE TABLE IF NOT EXISTS sessions(token TEXT PRIMARY KEY,user TEXT REFERENCES users(id));
 CREATE TABLE IF NOT EXISTS trips(id TEXT PRIMARY KEY,name TEXT,currency TEXT,owner TEXT REFERENCES users(id));
 CREATE TABLE IF NOT EXISTS members(trip TEXT REFERENCES trips(id),user TEXT REFERENCES users(id),PRIMARY KEY(trip,user));
 CREATE TABLE IF NOT EXISTS invites(token TEXT PRIMARY KEY,trip TEXT REFERENCES trips(id),email TEXT,used INTEGER DEFAULT 0);
 CREATE TABLE IF NOT EXISTS entries(id TEXT PRIMARY KEY,trip TEXT REFERENCES trips(id),author TEXT REFERENCES users(id),kind TEXT,data TEXT,created TEXT);
 CREATE TABLE IF NOT EXISTS history(id INTEGER PRIMARY KEY,trip TEXT,actor TEXT,action TEXT,detail TEXT,created TEXT);
 CREATE TABLE IF NOT EXISTS notices(id INTEGER PRIMARY KEY,user TEXT,text TEXT,created TEXT,seen INTEGER DEFAULT 0);
 ''')
def entries(c,t,u=None):
 a=rows(c,'SELECT * FROM entries WHERE trip=? ORDER BY created DESC',(t,))
 for e in a:e['data']=json.loads(e['data'])
 return [e for e in a if e['kind']!='private' or e['author']==u]
def calc(es,ms):
 b={m['id']:0 for m in ms};cash=con=spent=personal=total=0
 for e in es:
  d=e['data'];a=d['base'];k=e['kind']
  if k=='contribution':cash+=a;con+=a;b[d['payer']]+=a
  if k=='withdrawal':cash-=a;b[d['payer']]-=a
  if k=='expense':
   total+=a
   for uid,v in d['shares'].items():b[uid]-=v
   if d['source']=='cash':cash-=a;spent+=a
   else:b[d['payer']]+=a;personal+=a
  if k=='transfer' and d['status']=='confirmed':b[d['payer']]+=a;b[d['recipient']]-=a
 return dict(balances=b,cash=cash,contributions=con,cashSpent=spent,personal=personal,total=total)
def validate_pending(es,ms):
 s=calc(es,ms);out={};inc={}
 for e in es:
  if e['kind']=='transfer' and e['data']['status']=='pending':
   v=e['data'];out[v['payer']]=out.get(v['payer'],0)+v['base'];inc[v['recipient']]=inc.get(v['recipient'],0)+v['base']
 assert all(a<=max(0,-s['balances'][u]) for u,a in out.items()) and all(a<=max(0,s['balances'][u]) for u,a in inc.items()),'Изменение затрагивает ожидающий перевод. Сначала подтвердите получение.'
class Handler(SimpleHTTPRequestHandler):
 def __init__(self,*a,**kw):super().__init__(*a,directory=str(ROOT/'static'),**kw)
 def log_message(self,*a):pass
 def send(self,status,obj):
  b=json.dumps(obj,ensure_ascii=False).encode();self.send_response(status);self.send_header('Content-Type','application/json; charset=utf-8');self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff');self.send_header('Content-Length',str(len(b)));self.end_headers();self.wfile.write(b)
 def do_GET(self):
  if self.path.startswith('/api/'):self.api()
  else:
   self.path=self.path.split('?')[0]
   if self.path=='/':self.path='/index.html'
   super().do_GET()
 def do_POST(self):self.api()
 def api(self):
  try:
   with db() as c:
    if self.command=='POST':
     assert int(self.headers.get('Content-Length','0'))<4500000,'Файл слишком большой'
     origin=self.headers.get('Origin');assert not origin or origin.split('://')[-1]==self.headers.get('Host'),'Недопустимый источник запроса'
     d=json.loads(self.rfile.read(int(self.headers.get('Content-Length',0))) or '{}')
    else:d={}
    path=self.path.split('?')[0];cookie=SimpleCookie(self.headers.get('Cookie',''));token=cookie.get('session');u=c.execute('SELECT users.* FROM users JOIN sessions ON sessions.user=users.id WHERE token=?',(token.value if token else '',)).fetchone()
    if path in ['/api/register','/api/login']:
     email=d['email'].strip().lower();assert '@' in email and len(email)<255,'Укажите email';pw=d['password'];assert len(pw)>=10,'Пароль — минимум 10 символов'
     if path.endswith('register'):
      salt=secrets.token_hex(16);uid=secrets.token_hex(12);name=d['name'].strip();assert 0<len(name)<=80,'Укажите имя'
      c.execute('INSERT INTO users(id,email,name,password,salt) VALUES(?,?,?,?,?)',(uid,email,name,hashlib.pbkdf2_hmac('sha256',pw.encode(),salt.encode(),600000).hex(),salt))
     else:
      user=c.execute('SELECT * FROM users WHERE email=?',(email,)).fetchone();assert user and hmac.compare_digest(user['password'],hashlib.pbkdf2_hmac('sha256',pw.encode(),user['salt'].encode(),600000).hex()),'Неверный email или пароль';uid=user['id']
     t=secrets.token_urlsafe(32);c.execute('INSERT INTO sessions VALUES(?,?)',(t,uid));c.commit();self.send_response(200);self.send_header('Set-Cookie','session='+t+'; HttpOnly; SameSite=Lax; Path=/'+('; Secure' if os.environ.get('COOKIE_SECURE')=='1' else ''));self.end_headers();self.wfile.write(b'{}');return
    if not u:self.send(401,{'error':'Войдите в аккаунт'});return
    uid=u['id']
    if path=='/api/logout':c.execute('DELETE FROM sessions WHERE token=?',(token.value,));self.send(200,{});return
    if path=='/api/me':
     self.send(200,dict(user={k:u[k] for k in ['id','name','email','notifications']},trips=rows(c,'SELECT trips.* FROM trips JOIN members ON trips.id=members.trip WHERE members.user=?',(uid,)),notices=rows(c,'SELECT * FROM notices WHERE user=? ORDER BY id DESC LIMIT 100',(uid,))));return
    if path=='/api/preferences':c.execute('UPDATE users SET notifications=? WHERE id=?',(int(bool(d['notifications'])),uid));c.execute('UPDATE notices SET seen=1 WHERE user=?',(uid,));self.send(200,{});return
    if path=='/api/trips':
     t=secrets.token_hex(12);assert d['name'].strip(),'Укажите название';cur=d['currency'].upper();assert len(cur)==3 and cur.isalpha(),'Используйте код валюты из трёх букв'
     c.execute('INSERT INTO trips VALUES(?,?,?,?)',(t,d['name'][:100],cur,uid));c.execute('INSERT INTO members VALUES(?,?)',(t,uid));self.send(200,{'id':t});return
    if path=='/api/accept':
     c.execute('BEGIN IMMEDIATE');inv=c.execute('SELECT * FROM invites WHERE token=? AND used=0',(d['token'],)).fetchone();assert inv,'Приглашение не найдено или уже принято';t=inv['trip'];assert not inv['email'] or inv['email']==u['email'],'Приглашение для другого email'
     assert c.execute('SELECT COUNT(*) FROM members WHERE trip=?',(t,)).fetchone()[0]<10,'В поездке уже 10 участников'
     c.execute('INSERT INTO members VALUES(?,?)',(t,uid));c.execute('UPDATE invites SET used=1 WHERE token=?',(d['token'],));self.audit(c,t,uid,'Присоединился к поездке','');self.send(200,{'id':t});return
    t=d.get('trip') or (self.path.split('trip=')[-1] if 'trip=' in self.path else '')
    assert c.execute('SELECT 1 FROM members WHERE trip=? AND user=?',(t,uid)).fetchone(),'Нет доступа к поездке'
    trip=dict(c.execute('SELECT * FROM trips WHERE id=?',(t,)).fetchone());ms=rows(c,'SELECT users.id,users.name,users.email FROM users JOIN members ON users.id=members.user WHERE members.trip=?',(t,));ids={m['id'] for m in ms};es=entries(c,t,uid)
    if path=='/api/trip':self.send(200,dict(trip=trip,members=ms,entries=es,summary=calc(es,ms),history=rows(c,'SELECT history.*,users.name FROM history JOIN users ON actor=users.id WHERE trip=? ORDER BY history.id DESC LIMIT 200',(t,))));return
    c.execute('BEGIN IMMEDIATE')
    if path=='/api/invite':
     assert trip['owner']==uid,'Приглашает организатор';tok=secrets.token_urlsafe(24);email=d.get('email','').lower().strip();c.execute('INSERT INTO invites VALUES(?,?,?,0)',(tok,t,email))
     for r in rows(c,'SELECT id FROM users WHERE email=?',(email,)):self.notice(c,r['id'],'Приглашение в «'+trip['name']+'». Код: '+tok)
     self.audit(c,t,uid,'Создал приглашение',email);self.send(200,{'token':tok});return
    if path=='/api/confirm':
     e=next((e for e in es if e['id']==d['id']),None);assert e and e['kind']=='transfer','Перевод не найден';v=e['data'];assert v['recipient']==uid and v['status']=='pending','Подтвердить может получатель, один раз';v['status']='confirmed';c.execute('UPDATE entries SET data=? WHERE id=?',(json.dumps(v),e['id']));self.audit(c,t,uid,'Подтвердил получение',str(v['base']/100));self.notice(c,v['payer'],'Получение перевода подтверждено');self.send(200,{});return
    if path=='/api/delete':
     e=next((e for e in es if e['id']==d['id']),None);assert e and e['author']==uid,'Удалить может автор';assert e['kind'] in ['expense','private'],'Финансовые движения не удаляются';after=[x for x in es if x['id']!=e['id']];validate_pending(after,ms);assert calc(after,ms)['cash']>=0,'Удаление нарушает остаток кассы';c.execute('DELETE FROM entries WHERE id=?',(e['id'],))
     if e['kind']!='private':self.audit(c,t,uid,'Удалил расход',json.dumps(e['data'],ensure_ascii=False));self.notify_members(c,ids,uid,'Удалён расход «'+e['data']['title']+'»')
     self.send(200,{});return
    assert path=='/api/entry','Неизвестная операция';k=d['kind'];assert k in ['expense','private','contribution','withdrawal','transfer'],'Неверный тип';v=d['data'];a=money(v['amount']);currency=v.get('currency',trip['currency']).upper();rate=Decimal(str(v.get('rate',1)));assert rate.is_finite() and rate>0,'Введите положительный курс';assert currency!=trip['currency'] or rate==1,'Курс основной валюты равен 1';base=int((Decimal(a)*rate).quantize(Decimal('1'),rounding=ROUND_HALF_UP));assert base>0,'Сумма меньше копейки';v.update(base=base,original=a,currency=currency,rate=str(rate));datetime.date.fromisoformat(v['date']);v['title']=v.get('title','').strip()[:160]
    old=next((e for e in es if e['id']==d.get('id')),None)
    if d.get('id'):assert old and old['author']==uid and old['kind']==k and k in ['expense','private'],'Нет прав на изменение'
    if k in ['contribution','withdrawal','transfer']:assert currency==trip['currency'],'Взносы и возвраты проводятся в основной валюте';assert v['payer'] in ids,'Неизвестный участник'
    if k in ['contribution','withdrawal']:assert v['payer']==uid or trip['owner']==uid,'Операцию выполняет участник или организатор'
    if k in ['expense','private']:
     assert v['title'],'Введите название';receipt=v.get('receipt','');assert not receipt or (len(receipt)<4000000 and receipt.startswith(('data:image/png;base64,','data:image/jpeg;base64,','data:image/webp;base64,'))),'Чек: JPG, PNG или WebP до 3 МБ'
     if k=='private':v['source']='personal';v['payer']=uid;v['shares']={uid:base}
     else:
      assert v['source'] in ['cash','personal'],'Выберите источник';assert v['source']=='cash' or v['payer'] in ids,'Выберите плательщика';selected=v['participants'];assert selected and len(set(selected))==len(selected) and set(selected)<=ids,'Выберите участников'
      if v.get('custom'):
       assert all(Decimal(str(v['custom'][x]))>=0 for x in selected),'Доли не могут быть отрицательными'
       shares={x:money(v['custom'][x]) if Decimal(str(v['custom'][x]))>0 else 0 for x in selected};assert sum(shares.values())==base,'Сумма долей в основной валюте должна совпадать с расходом';v['shares']=shares
      else:q,r=divmod(base,len(selected));v['shares']={x:q+(i<r) for i,x in enumerate(selected)}
    current=[x for x in entries(c,t,uid) if x['id']!=d.get('id')];s=calc(current,ms)
    if k=='transfer':
     assert v['payer']==uid and v['recipient'] in ids and v['recipient']!=uid,'Неверные стороны перевода';pending=[e for e in current if e['kind']=='transfer' and e['data']['status']=='pending'];reserved=sum(e['data']['base'] for e in pending if e['data']['payer']==uid);recReserved=sum(e['data']['base'] for e in pending if e['data']['recipient']==v['recipient']);assert base<=max(0,-s['balances'][uid])-reserved and base<=max(0,s['balances'][v['recipient']])-recReserved,'Сумма превышает свободное обязательство с учётом ожидающих переводов';v['status']='pending'
    if k=='withdrawal':assert base<=max(0,s['balances'][v['payer']]),'Нельзя вывести больше положительного баланса'
    eid=d.get('id') or secrets.token_hex(12);item=dict(id=eid,trip=t,author=uid,kind=k,data=v,created=old['created'] if old else now());assert calc(current+[item],ms)['cash']>=0,'В кассе недостаточно денег. Добавьте взнос или выберите личные деньги.'
    validate_pending(current+[item],ms)
    c.execute('INSERT OR REPLACE INTO entries VALUES(?,?,?,?,?,?)',(eid,t,uid,k,json.dumps(v,ensure_ascii=False),item['created']))
    if k!='private':self.audit(c,t,uid,'Изменил запись' if old else 'Добавил запись',json.dumps({'before':old['data'] if old else None,'after':v},ensure_ascii=False));self.notify_members(c,ids,uid,('Изменена' if old else 'Добавлена')+' операция: '+(v['title'] or k))
    self.send(200,{'id':eid})
  except (AssertionError,ValueError,KeyError,ArithmeticError,sqlite3.IntegrityError) as e:self.send(400,{'error':str(e) or 'Проверьте поля'})
  except Exception as e:self.send(500,{'error':'Ошибка сервера. Проверьте данные и повторите.'})
 def audit(self,c,t,u,a,d):c.execute('INSERT INTO history(trip,actor,action,detail,created) VALUES(?,?,?,?,?)',(t,u,a,d,now()))
 def notice(self,c,u,msg):c.execute('INSERT INTO notices(user,text,created) SELECT id,?,? FROM users WHERE id=? AND notifications=1',(msg,now(),u))
 def notify_members(self,c,ids,u,msg):
  for x in ids-{u}:self.notice(c,x,msg)
if __name__=='__main__':
 init();print('Попутчики: http://localhost:8000',flush=True);ThreadingHTTPServer((os.environ.get('HOST','127.0.0.1'),int(os.environ.get('PORT','8000'))),Handler).serve_forever()

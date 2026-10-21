import unittest,tempfile,threading,json,urllib.request,urllib.error,http.cookiejar,os
import server
class AppTests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  cls.temp=tempfile.TemporaryDirectory();server.DB=cls.temp.name+'/test.db';server.init();cls.http=server.ThreadingHTTPServer(('127.0.0.1',0),server.Handler);threading.Thread(target=cls.http.serve_forever,daemon=True).start();cls.url='http://127.0.0.1:'+str(cls.http.server_port)
 @classmethod
 def tearDownClass(cls):cls.http.shutdown();cls.http.server_close();cls.temp.cleanup()
 def test_full_journey(self):
  def client():return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
  def req(c,p,d=None):
   r=c.open(urllib.request.Request(self.url+'/api/'+p,data=json.dumps(d).encode() if d is not None else None,headers={'Content-Type':'application/json'}));return json.loads(r.read())
  clients=[];users=[]
  for i in range(11):
   c=client();req(c,'register',{'name':'Участник '+str(i),'email':str(i)+'@test.ru','password':'password12345'});clients.append(c);users.append(req(c,'me')['user']['id'])
  a=clients[0];t=req(a,'trips',{'name':'Горы','currency':'RUB'})['id']
  destination=req(a,'trips',{'name':'Стамбул','currency':'TRY','country':'Турция','city':'Стамбул'})['id']
  saved=req(a,'trip?trip='+destination)['trip'];self.assertEqual((saved['country'],saved['city'],saved['currency']),('Турция','Стамбул','TRY'))
  with self.assertRaises(urllib.error.HTTPError):req(a,'trips',{'name':'Ошибка','currency':'EUR','country':'Турция','city':'Париж'})
  for i in range(1,10):tok=req(a,'invite',{'trip':t})['token'];req(clients[i],'accept',{'token':tok})
  tok=req(a,'invite',{'trip':t})['token']
  with self.assertRaises(urllib.error.HTTPError):req(clients[10],'accept',{'token':tok})
  def add(c,k,amount,**kw):
   d=dict(amount=amount,date='2026-10-21',currency='RUB',rate=1,payer=users[0],title='Тест',source='cash',participants=users[:4],category='Кафе');d.update(kw);return req(c,'entry',dict(trip=t,kind=k,data=d))['id']
  add(a,'contribution',6000);add(clients[1],'contribution',2000,payer=users[1]);e=add(a,'expense',1000)
  s=req(a,'trip?trip='+t);self.assertEqual(s['summary']['cash'],700000);self.assertEqual(s['summary']['total'],100000);self.assertEqual(s['summary']['balances'][users[2]],-25000)
  with self.assertRaises(urllib.error.HTTPError):add(a,'expense',8000)
  # Foreign amount, frozen rate, individually assigned base currency shares.
  fx=add(a,'expense',10,currency='EUR',rate=100,source='personal',participants=users[:2],custom={users[0]:300,users[1]:700})
  self.assertEqual(req(a,'trip?trip='+t)['summary']['personal'],100000)
  # Editing membership recalculates balances and retains audit.
  req(a,'entry',dict(trip=t,id=e,kind='expense',data=dict(amount=1000,date='2026-10-21',currency='RUB',rate=1,title='Обновлено',source='cash',participants=users[:2],category='Кафе')))
  self.assertEqual(req(a,'trip?trip='+t)['summary']['balances'][users[2]],0)
  private=add(a,'private',123,title='Секрет')
  other=req(clients[1],'trip?trip='+t);self.assertNotIn(private,[x['id'] for x in other['entries']]);self.assertNotIn('Секрет',json.dumps(other,ensure_ascii=False))
  with self.assertRaises(urllib.error.HTTPError):req(clients[1],'delete',{'trip':t,'id':private})
  # Return reserve explicitly, then personal IOUs can be settled.
  add(a,'withdrawal',6000,payer=users[0])
  # At this point user 1 still positive; add personal expenses payable by user 0 until user 1 owes.
  add(a,'expense',4000,source='personal',participants=[users[1]])
  tr=add(clients[1],'transfer',100,payer=users[1],recipient=users[0]);before=req(a,'trip?trip='+t)['summary']['balances'][users[1]]
  with self.assertRaises(urllib.error.HTTPError):req(clients[1],'confirm',{'trip':t,'id':tr})
  req(a,'confirm',{'trip':t,'id':tr});self.assertEqual(req(a,'trip?trip='+t)['summary']['balances'][users[1]],before+10000)
  with self.assertRaises(urllib.error.HTTPError):req(a,'confirm',{'trip':t,'id':tr})
  s=req(a,'trip?trip='+t);self.assertEqual(sum(s['summary']['balances'].values()),s['summary']['cash']);self.assertEqual(s['summary']['total'],600000)
  # One-person trip and rounding.
  solo=req(a,'trips',{'name':'Один','currency':'RUB'})['id'];req(a,'entry',dict(trip=solo,kind='expense',data=dict(amount='1.01',currency='RUB',rate=1,date='2026-10-21',title='Один',source='personal',payer=users[0],participants=[users[0]])));self.assertEqual(req(a,'trip?trip='+solo)['summary']['balances'][users[0]],0)
  with self.assertRaises(urllib.error.HTTPError):req(clients[1],'trip?trip='+solo)
if __name__=='__main__':unittest.main()

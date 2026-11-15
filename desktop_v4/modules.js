'use strict';
(()=>{
 let S={};
 const reset=()=>{stopTraffic();S={}};
 const op=(method,args={})=>rpc('operation',method,args);
 const button=(name,fn,cls='quiet',disabled=false)=>{const b=btn(name,cls,fn);b.disabled=disabled;return b};
 const live=(text,fn,cls='quiet')=>button(text,fn,cls,preview);
 const field=(title,type='text',value='',opts=[])=>{
  const input=el(type==='select'?'select':type==='textarea'?'textarea':'input');
  if(type==='select'){for(const [v,n] of opts){const o=el('option','',n);o.value=String(v);input.append(o)}}
  else if(type!=='textarea')input.type=type;
  if(value!=='')input.value=String(value);
  return {input,node:add(el('label','',title),input)};
 };
 const form=(root,...fields)=>{const p=el('div','panel fields');p.append(...fields.map(f=>f.node||f));root.append(p);return p};
 const action=(root,button)=>{root.append(add(el('div','actionbar'),button));return button};
 const section=(root,title)=>root.append(el('h2','section-head',title));
 const value=(f)=>f.input.value.trim();
 const num=f=>Number(f.input.value);
 const error=err=>toast(err?.message||'Операция не выполнена.');
 const run=async(fn)=>{const owner=identity;try{return await fn()}catch(err){if(identity===owner)error(err)}finally{if(identity===owner&&owner)render(false)}};
 const keyValue=(root,entries)=>{const d=el('div','kv-grid');for(const [k,v] of entries)d.append(add(el('div','kv'),el('small','',k),el('strong','',v==null||v===''?'—':Array.isArray(v)?v.join(', '):String(v))));root.append(d)};
 const status=v=>el('span',v?'online':'offline',v?'В сети':'Не в сети');
 const serverField=(title='VPN-сервер')=>field(title,'select',S.host||'',(S.hosts||[]).map(r=>[r.host,r.host+' · '+r.country]));
 const ensureHosts=(root)=>{if(S.hosts?.length)return true;root.append(notice('Загрузите список доступных серверов.'));action(root,live('Загрузить серверы',()=>run(async()=>{S.hosts=await rpc('servers');S.host=S.hosts[0]?.host||''}),'primary'));return false};
 const setHost=f=>{S.host=value(f);return S.host};
 const previewNotice=(root,plan,onConfirm)=>{if(!plan)return;const p=el('div','panel confirmation');p.append(el('p','eyebrow','ПОДТВЕРЖДЕНИЕ'),el('h3','',plan.label),el('p','','Одноразовое разрешение действует 120 секунд. Проверьте параметры до отправки.'));const b=live('Подтвердить',()=>run(async()=>{const token=plan.token;S.plan=null;try{S.outcome=await op('commit',{token});if(onConfirm)onConfirm(S.outcome)}finally{if(identity)render(false)}}),'primary');p.append(add(el('div','actionbar'),b,button('Отменить',()=>{S.plan=null;render(false)})));root.append(p)};
 const stage=(method,args)=>{const from=page;stopTraffic();return run(async()=>{S.plan=null;S.outcome=null;S.plan=await op(method,args);S.planPage=from})};
 const currentPlan=()=>S.planPage===page?S.plan:null;
 const resultNote=(root)=>{if(S.outcome&&S.planPage===page)root.append(notice('Операция завершена. Проверьте актуальное состояние целевой записи.'))};
 function imagePreview(root,result){if(!result?.src||!/^data:image\/(png|jpeg|webp);base64,/.test(result.src))return;const i=el('img','camera-thumb');i.alt='Кадр камеры';i.src=result.src;root.append(add(el('div','panel camera-preview'),el('h3','','Превью камеры'),i))}
 function create(root){
  root.append(hero('VPN / PROVISIONING','Создание VPN-клиентов','Используется существующий распределитель адресов и портов RouterOS.'));
  if(!ensureHosts(root))return;
  const h=serverField(),l=field('Базовый логин','text',S.createLogin||''),p=field('Портов на учётку','number',S.createPorts||2),c=field('Количество учёток','number',S.createCount||1),ip=field('Начальный Remote Address','text',S.createIP||'172.16.0.1');
  p.input.min=1;p.input.max=14;c.input.min=1;c.input.max=20;const countries=[...new Set(S.hosts.map(x=>x.country))],region=field('Регион для автоподбора','select',S.createRegion||countries[0],countries.map(x=>[x,x]));
  form(root,h,l,p,c,ip,region);action(root,live('Предложить свободный логин',()=>run(async()=>{S.createIP=value(ip);S.createPorts=num(p);S.createCount=num(c);S.createLogin=(await op('create_suggest',{host:setHost(h),login:value(l)})).login})));action(root,live('Подобрать свободный сервер',()=>run(async()=>{S.createLogin=value(l);S.createPorts=num(p);S.createCount=num(c);S.createRegion=value(region);S.createIP=value(ip);const result=await op('create_auto',{country:S.createRegion});S.host=result.host;toast('Выбран '+result.host)})));action(root,live('Подготовить создание',()=>{S.createLogin=value(l);S.createPorts=num(p);S.createCount=num(c);S.createIP=value(ip);stage('create_preview',{host:setHost(h),login:S.createLogin,ports:S.createPorts,count:S.createCount,start_ip:S.createIP})},'primary'));
  previewNotice(root,currentPlan());resultNote(root);
  if(S.planPage==='create'&&S.outcome?.clients)root.append(table(['Логин','Сервер','VPN IP','Порты',''],S.outcome.clients.map(c=>[c.login,c.server,c.remote_address,c.ports.join(', '),live('Копировать VPN',()=>rpc('copyVpn',c.server,c.login).then(()=>toast('Скопировано')).catch(error))])));
 }
 async function client(host,login){stopTraffic();S.clientHistory=null;S.camPorts=null;S.cameraLogin='';S.client=null;S.plan=null;S.outcome=null;await run(async()=>{S.client=await op('client_detail',{host,login})})}
 let traffic=null;
 function stopTraffic(){if(traffic){traffic.stopped=true;clearTimeout(traffic.timer);traffic=null}}
 function trafficPanel(root,c){
  if(traffic&&(traffic.host!==c.server||traffic.login!==c.login))stopTraffic();
  const card=el('div','panel traffic-panel'),title=add(el('div','traffic-head'),el('h3','','Трафик VPN'),live(traffic?'Остановить':'Начать мониторинг',()=>{if(traffic){stopTraffic();render(false);return}startTraffic(c);render(false)},'primary'));
  card.append(title);const view=el('div');view.id='traffic-view';card.append(view,el('p','muted','Общий RX/TX VPN-сессии, не отдельных NAT-портов. Обновление каждые 2,5 сек.'));root.append(card);drawTraffic();
 }
 function drawTraffic(){
  const view=$('traffic-view');if(!view)return;view.replaceChildren();
  if(!traffic){view.append(el('p','muted','Мониторинг выключен.'));return}
  const x=traffic,s=x.last,pts=x.points,fmt=n=>n==null?'—':n.toFixed(2)+' Мбит/с',last=pts.at(-1)||{};
  keyValue(view,[['Получение',fmt(last.rx)],['Отправка',fmt(last.tx)],['Состояние',x.error?'Нет подтверждения':!s?'Ожидание':s.online?'Онлайн':'Офлайн'],['Uptime',s?.uptime]]);
  if(x.error)view.append(notice(x.error));
  const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');svg.setAttribute('viewBox','0 0 800 200');svg.setAttribute('class','traffic-chart');svg.setAttribute('role','img');svg.setAttribute('aria-label','График скорости RX и TX VPN');
  const max=Math.max(1,...pts.flatMap(p=>[p.rx||0,p.tx||0]))*1.1;const node=(name,attrs)=>{const n=document.createElementNS(svg.namespaceURI,name);for(const [k,v] of Object.entries(attrs))n.setAttribute(k,String(v));return n};
  for(let z=0;z<5;z++){const y=10+z*42;svg.append(node('line',{x1:54,y1:y,x2:790,y2:y,class:'traffic-grid'}));const label=node('text',{x:4,y:y+4,class:'traffic-axis'});label.textContent=(max*(1-z/4)).toFixed(1);svg.append(label)}
  for(const [key,cls] of [['rx','rx-line'],['tx','tx-line']]){let line='';let pen=false;for(let i=0;i<pts.length;i++){const p=pts[i];if(p[key]==null){pen=false;continue}const x=54+i*736/59,y=178-p[key]/max*168;line+=(pen?' L ':' M ')+x+' '+y;pen=true}if(line)svg.append(node('path',{d:line,class:cls,fill:'none','stroke-width':2.4,'stroke-linecap':'round','stroke-linejoin':'round'}))}
  view.append(svg,add(el('div','traffic-legend'),el('span','','RX · получение'),el('span','','TX · отправка')));
 }
 function startTraffic(c){
  stopTraffic();const x={host:c.server,login:c.login,owner:identity,stopped:false,last:null,points:[],error:null};traffic=x;
  async function poll(){
   if(x.stopped||traffic!==x)return;
   if(page!=='search'||identity!==x.owner||!S.client||S.client.server!==x.host||S.client.login!==x.login){stopTraffic();return}
   if(document.hidden||taskBusy){x.timer=setTimeout(poll,2500);return}
   taskBusy=true;
   try{
    const result=await window.helper.operation('traffic_sample',{host:x.host,login:x.login});if(!result.ok)throw Error(result.error);
    if(x.stopped||traffic!==x)return;
    const s=result.data,p=x.last,dt=p?s.sample_time-p.sample_time:0;
    const valid=p&&s.online&&p.online&&s.interface===p.interface&&dt>=.2&&s.rx>=p.rx&&s.tx>=p.tx;
    x.points.push({rx:valid?(s.rx-p.rx)*8/dt/1e6:null,tx:valid?(s.tx-p.tx)*8/dt/1e6:null});if(x.points.length>60)x.points.shift();x.last=s;x.error=null;
   }catch(err){if(traffic===x){x.error='Не удалось получить новый образец трафика.';x.last=null;x.points.push({rx:null,tx:null});if(x.points.length>60)x.points.shift()}}
   finally{taskBusy=false;if(!x.stopped&&traffic===x){drawTraffic();x.timer=setTimeout(poll,2500)}}
  }
  x.timer=setTimeout(poll,10);
 }
 function renderClient(root){
  const c=S.client;if(!c){if(currentPlan())previewNotice(root,currentPlan(),out=>{if(out?.restored&&out.client){S.client=out.client;S.camPorts=null;S.clientHistory=null;searchState=null}});resultNote(root);return;}
  section(root,'Карточка VPN · '+c.login);
  const panel=el('div','panel');keyValue(panel,[['Сервер',c.server],['VPN IP',c.remote_address],['IP источника',c.tunnel_source_ip],['Статус',c.is_online?'В сети':'Не в сети'],['Учётка',c.is_enabled?'Включена':'Отключена'],['Uptime',c.uptime],['RX, байт',c.rx_bytes],['TX, байт',c.tx_bytes]]);
  panel.append(table(['Порт','Статус','Байты NAT','Пакеты NAT','Конфликты'],c.ports.map(p=>[p,c.disabled_ports.includes(p)?'Выключен':c.active_ports.includes(p)?'Активен':'Настроен',c.port_nat_bytes?.[p]??'—',c.port_nat_packets?.[p]??'—',c.conflicts_verified===false?'Не проверено':c.port_conflicts?.[p]||'—'])));
  root.append(panel);
  trafficPanel(root,c);
  const cameraLogin=field("Уточнить логин B2O (необязательно)","text",S.cameraLogin||"");form(root,cameraLogin);action(root,live("Сверить NAT с камерами",()=>run(async()=>{S.cameraLogin=value(cameraLogin);S.camPorts=null;S.camPorts=await op("camera_ports",{host:c.server,login:c.login,override:S.cameraLogin})}),"primary"));
  if(S.camPorts){const q=S.camPorts;keyValue(root,[["Камер проверено",q.checked_cameras],["Подтверждено",q.matched_camera_count],["Ошибок",q.stream_errors]]);const linked=[...q.links,...q.grouped.flatMap(group=>group[1])];if(linked.length)root.append(table(["NAT-порт","ID камеры","Камера","Страна"],linked.map(x=>[x.port,x.camera_id,x.label,x.country])));if(q.ambiguous.length)root.append(notice("Неоднозначные порты: "+q.ambiguous.join(", ")));if(q.unmatched.length)root.append(notice("Без подтверждения: "+q.unmatched.join(", ")));if(q.incomplete)root.append(notice("Ответ B2O неполный. Не подтверждает отсутствие камер."))}
  action(root,live('Копировать VPN',()=>rpc('copyVpn',c.server,c.login).then(()=>toast('Конфигурация скопирована')).catch(error)));
  if(identity?.role==='sysadmin')action(root,live('WinBox',()=>run(()=>op('winbox',{host:c.server}))));
  const a=field('Действие','select','add',[['add','Добавить порты'],['disconnect','Разорвать сессию'],...(identity?.role==='sysadmin'?[['enable','Включить VPN'],['disable','Отключить VPN'],['port_enable','Включить порт'],['port_disable','Отключить порт'],['remove_port','Удалить порт'],['recreate_port','Пересоздать порт'],['password','Сменить пароль']]:[])]),p=field('Порт / количество','number',1),pw=field('Новый пароль','password');
  form(root,a,p,pw);action(root,live('Подготовить действие',()=>{const args={host:c.server,login:c.login,action:value(a),count:num(p),port:num(p),password:pw.input.value};pw.input.value='';stage('vpn_preview',args)},'primary'));action(root,live('Обновить карточку',()=>client(c.server,c.login)));
  if(identity?.role==='sysadmin'){const d=field('Для удаления: точный server/login','text');form(root,d);action(root,live('Подготовить удаление',()=>stage('delete_preview',{host:c.server,login:c.login,confirm:value(d)})))}
  previewNotice(root,currentPlan(),out=>{if(out?.deleted){stopTraffic();S.client=null;searchState=null}else if(out?.login){S.client=out;S.camPorts=null;S.clientHistory=null}});resultNote(root);clientHistory(root,c);
 }
 function inactive(root){
  root.append(hero('VPN / LIFECYCLE','Неактивные VPN','Жизненный цикл учёток на всех серверах. Частичный ответ не считается полным результатом.'));
  if(!preview&&identity?.role!=='sysadmin'){root.append(notice('Раздел доступен системному администратору.'));return}
  if(!ensureHosts(root))return;
  const h=serverField(),days=field('Порог неактивности, дней','number',S.inactiveDays||30),scope=field('Область проверки','select',S.inactiveScope||'all',[['all','Все доверенные серверы'],['one','Выбранный сервер']]),unknown=field('Неизвестное состояние','select',S.inactiveUnknown||'yes',[['yes','Показать'],['no','Скрыть']]);form(root,scope,h,days,unknown);
  action(root,live('Проверить',()=>run(async()=>{S.inactiveDays=num(days);S.inactiveScope=value(scope);S.inactiveUnknown=value(unknown);S.inactiveSelected=[];S.inactive=null;S.inactiveBatch=null;S.plan=null;S.inactive=await op('inactive_fleet',{host:setHost(h),all:S.inactiveScope==='all',days:S.inactiveDays,unknown:S.inactiveUnknown==='yes'})}),'primary'));
  if(S.inactive){const result=S.inactive,labels={A:'Активная',S:'Сон',Q:'Карантин',R:'Архив',M:'Ручная',U:'Неизвестно'};keyValue(root,[['Найдено',result.rows.length],['Серверов',result.total],['Ошибок',result.errors.length]]);if(result.errors.length)root.append(notice('Недоступны: '+result.errors.join(', ')));if(result.cancelled)root.append(notice('Проверка отменена, вывод неполный.'));
   const filter=field('Состояние LV','select',S.lifeFilter||'', [['','Все'],...Object.entries(labels)]),order=field('Сортировка','select',S.lifeOrder||'old',[['old','Сначала старые'],['new','Сначала новые'],['server','По серверу'],['login','По логину']]),query=field('Фильтр логина','text',S.lifeQuery||'');form(root,filter,order,query);action(root,button('Применить фильтр',()=>{S.lifeFilter=value(filter);S.lifeOrder=value(order);S.lifeQuery=value(query);render(false)}));
   const rows=result.rows.filter(x=>(!S.lifeFilter||x.lifecycle_state===S.lifeFilter)&&(!S.lifeQuery||x.login.toLowerCase().includes(S.lifeQuery.toLowerCase()))).sort((a,b)=>S.lifeOrder==='new'?String(b.last_seen||'').localeCompare(a.last_seen||''):S.lifeOrder==='server'?a.server.localeCompare(b.server):S.lifeOrder==='login'?a.login.localeCompare(b.login):String(a.last_seen||'9999').localeCompare(b.last_seen||'9999'));const items=(S.inactiveSelected||[]),key=x=>x.server+'|'+x.login;
   root.append(table(['','Логин','Сервер','Состояние LV','Последний выход','Учётка'],rows.map(c=>{const ch=el('input','camera-check');ch.type='checkbox';ch.checked=items.some(x=>key(x)===key(c));ch.disabled=identity?.role!=='sysadmin'||c.is_online||result.cancelled;ch.setAttribute('aria-label','Выбрать '+c.login);ch.onchange=()=>{S.inactiveSelected=(S.inactiveSelected||[]).filter(x=>key(x)!==key(c));if(ch.checked)S.inactiveSelected.push(c);$('inactive-count').textContent='Выбрано: '+S.inactiveSelected.length};return [ch,c.login,c.server,labels[c.lifecycle_state]||c.lifecycle_state,c.last_logged_out||'—',c.is_enabled?'Включена':'Отключена']})));
   const count=el('span','muted','Выбрано: '+items.length);count.id='inactive-count';root.append(add(el('div','actionbar'),count,button('Выбрать до 50',()=>{S.inactiveSelected=rows.filter(x=>!x.is_online).slice(0,50);render(false)},'quiet',identity?.role!=='sysadmin'||result.cancelled),button('Снять выбор',()=>{S.inactiveSelected=[];render(false)})));
   if(identity?.role==='sysadmin'){
    for(const [act,label] of [['enable','Включить выбранные'],['disable','Отключить выбранные']])action(root,live(label,()=>stage('inactive_batch_preview',{action:act,targets:(S.inactiveSelected||[]).map(x=>({host:x.server,login:x.login}))})));
    const del=field('Для удаления введите УДАЛИТЬ и число выбранных','text');form(root,del);
    const selected=el('div','panel selected-targets');selected.append(el('h3','','Будут проверены только выбранные учётки'),el('p','muted','Удаление допускается только для офлайн-учёток с подтверждённой датой последнего выхода. Перед каждой записью — проверяемый FULL backup.'));
    const list=el('div');list.id='inactive-selected-targets';const renderList=()=>{list.replaceChildren();const targets=S.inactiveSelected||[];list.append(el('p','muted',targets.length?targets.map(x=>x.server+' / '+x.login).join('\n'):'Ничего не выбрано'))};selected.append(list);root.append(selected);renderList();
    for(const ch of root.querySelectorAll('input.camera-check'))ch.addEventListener('change',renderList);
    action(root,live('Подготовить удаление выбранных',()=>stage('inactive_batch_preview',{action:'delete',confirm:value(del),targets:(S.inactiveSelected||[]).map(x=>({host:x.server,login:x.login}))})));
   }
  }
  previewNotice(root,currentPlan(),out=>{S.inactiveBatch=out.items;S.inactive=null;S.inactiveSelected=[]});if(S.inactiveBatch){section(root,'Подтверждение массовых действий');const labels={confirmed:'Подтверждено',unknown:'Проверьте вручную',changed:'Состояние изменилось',not_attempted:'Не отправлено'};root.append(table(['Сервер','Логин','Результат'],S.inactiveBatch.map(x=>[x.server,x.login,labels[x.status]||x.status])))}resultNote(root);
 }
 function lv(root){
  root.append(hero('VPN / AUTOMATION','LV-автоматика','Состояние скриптов, планировщиков и жизненного цикла.'));
  if(!preview&&identity?.role!=='sysadmin'){root.append(notice('Раздел доступен системному администратору.'));return}
  if(!ensureHosts(root))return;
  const h=serverField(),a=field('Действие','select','install',[['install','Установить / обновить'],['start','Запустить'],['pause','Приостановить'],['seed','Инициализировать'],['quarantine_on','Включить карантин'],['quarantine_off','Выключить карантин']]);
  form(root,h,a);action(root,live('Проверить состояние',()=>run(async()=>{S.lv=await op('lv_status',{host:setHost(h)})}),'primary'));action(root,live('Подготовить изменение',()=>stage('lv_preview',{host:setHost(h),action:value(a)})));
  action(root,live('Проверить LV на всех серверах',()=>run(async()=>{S.lvFleet=await op('lv_fleet')}),'primary'));
  if(S.lvFleet){const data=S.lvFleet;root.append(table(['Сервер','Состояние','Версия','Инициализировано','Карантин'],data.rows.map(x=>[x.server,x.error?'Нет данных':x.state_text,x.version,x.initialized,x.error?'—':x.aging_enabled?'Включён':'Выключен'])));if(data.cancelled)root.append(notice('Проверка отменена.'));else for(const [act,label] of [['install','Установить / обновить на всех'],['start','Запустить установленную LV'],['pause','Остановить установленную LV']])action(root,live(label,()=>stage('lv_batch_preview',{action:act,hosts:act==='install'?S.hosts.map(x=>x.host):data.rows.filter(x=>x.installed).map(x=>x.server)})))}
  const x=S.lv;if(x)keyValue(root,[['Сервер',x.server],['Версия',x.version],['Скрипты',x.scripts_installed+' / '+x.scripts_expected],['Планировщики',x.schedulers_installed+' / '+x.schedulers_expected],['Установка',x.installed?'Подтверждена':'Не полная'],['Пауза',x.paused?'Да':'Нет'],['Инициализировано',x.initialized],['LV-Aging',x.aging_enabled?'Включён':'Выключен'],['Активные',x.active],['Сон',x.sleeping],['Карантин',x.quarantine],['Архив',x.archive],['Неизвестные',x.unknown]]);
  previewNotice(root,currentPlan(),out=>{if(out.lv_results){S.lvBatch=out.lv_results;S.lvFleet=null}else S.lv=out});if(S.lvBatch)root.append(table(['Сервер','Результат','Статус LV'],S.lvBatch.map(x=>[x.server,({confirmed:'Подтверждено',not_confirmed:'Не подтверждено',not_attempted:'Не отправлено'})[x.status],x.state])));resultNote(root);
 }
 function clearBillingView(){S.billingClient=null;S.hits=null;S.history=null;S.catalog=null;S.catalogFilter={};S.billingImage=null;S.bulkSelected=[];S.bulkResult=null;S.recipients=null;S.plan=null;S.outcome=null}
 function clearB2OView(){S.archive=null;S.archiveSaved=null;S.cameraInfo=null;S.cameraChoices=null;S.cameraLive=null;S.cameraImage=null;S.billingImage=null;S.agentImage=null;S.camPorts=null;S.plan=null;S.outcome=null}
 function sessionStrip(root,kind){
  const connected=!!S.admin?.[kind];const panel=el('div','panel session-strip');
  panel.append(add(el('div'),el('p','eyebrow','РАБОЧАЯ УЧЁТНАЯ ЗАПИСЬ'),el('h3','',kind==='crm'?'CRM / Биллинг':'B2O / Видео'),el('p','muted',connected?'Последний вход подтверждён · '+S.admin.username:'Подключение не подтверждено.')));
  const bar=el('div','actionbar');bar.append(live('Проверить сессию',()=>run(async()=>{S.admin=await op('admin_status')})),live('Повторить вход',()=>run(async()=>{if(kind==='crm')clearBillingView();else clearB2OView();S.admin=await op('admin_retry',{target:kind});for(const e of S.admin.errors||[])toast(e)})),button('Админка',()=>go('admin')));
  panel.append(bar);root.append(panel);
 }
 function admin(root){
  root.append(hero('HELPER / WORK ACCOUNTS','CRM / B2O','Две независимые корпоративные сессии. Данные MikroTik автоматически не используются.'));
  const u=field('Логин админки','text',S.admin?.username||''),p=field('Пароль','password'),t=field('Подключение','select','both',[['both','CRM и B2O'],['crm','Только CRM'],['b2o','Только B2O']]);
  const remember=field('Запомнить вход (Windows)','select','no',[['no','Нет'],['yes','Да']]);form(root,u,p,t,remember);action(root,live('Подключить',()=>{const password=p.input.value;p.input.value='';run(async()=>{clearBillingView();clearB2OView();S.admin=null;S.admin=await rpc('adminLogin',value(u),password,value(t),value(remember)==='yes')})},'primary'));
  action(root,live('Вход из хранилища',()=>run(async()=>{clearBillingView();clearB2OView();S.admin=null;S.admin=await rpc('adminSaved')})));
  action(root,live('Очистить сохранённый вход',()=>run(()=>rpc('clearSaved','admin'))));
  action(root,live('Проверить сессии',()=>run(async()=>{S.admin=await op('admin_status')})));
  action(root,live('Выйти из админки (без удаления сохранённых данных)',()=>run(async()=>{S.admin=await op('admin_logout');clearBillingView();clearB2OView()})));
  for(const k of ['crm','b2o'])action(root,live('Повторить '+k.toUpperCase(),()=>run(async()=>{if(k==='crm')clearBillingView();else clearB2OView();S.admin=await op('admin_retry',{target:k})}))); 
  if(S.admin){keyValue(root,[['Аккаунт',S.admin.username],['CRM',S.admin.crm?'Подключено':'Не подключено'],['B2O',S.admin.b2o?'Подключено':'Не подключено']]);for(const e of S.admin.errors||[])root.append(notice(e))}
 }
 function billing(root){
  root.append(hero('CRM / BILLING','Биллинг','Поиск клиента, карточка, история камеры и тарифный справочник BY / KZ.'));
  sessionStrip(root,'crm');
  const q=field('Логин клиента','text',S.billingQuery||'');
  form(root,q);action(root,live('Найти',()=>run(async()=>{S.billingQuery=value(q);clearBillingView();S.hits=await op('billing_search',{query:S.billingQuery})}),'primary'));
  action(root,live('Все тарифы',()=>run(async()=>{S.catalogFilter={};S.catalog=await op('billing_catalog',{scope:'all'})})));
  if(S.hits)root.append(table(['Логин','Клиент','Страна','Баланс',''],S.hits.map(x=>[x.login,x.account_name,x.country,x.balance_text,live('Открыть',()=>run(async()=>{S.billingClient=null;S.plan=null;S.history=null;S.catalog=null;S.catalogFilter={};S.billingImage=null;S.bulkSelected=[];S.bulkResult=null;S.recipients=null;S.billingClient=await op('billing_select',{id:x.id})}))])));
  const c=S.billingClient;if(c){section(root,c.display_name||c.login);keyValue(root,[['Логин',c.login],['Страна',c.country],['Статус',c.status],['Баланс',c.balance_text],['Договор',c.contract_number],['Тип',c.legal_type],['Услуги',c.services_loaded?c.services_status||c.services.length:'Не загружены'],['E-mail',c.email],['Телефон',c.phone],['Менеджер',c.manager],['Адрес',c.address],['Регистрация',c.registered_at]]);action(root,live('Обновить карточку',()=>run(async()=>{S.plan=null;S.catalog=null;S.catalogFilter={};S.history=null;S.billingImage=null;S.bulkSelected=[];S.recipients=null;S.billingClient=await op('billing_refresh')})));if(!c.services_loaded)root.append(notice('CRM не подтвердила загрузку списка камер.'));
   root.append(table(['','Камера','ID CRM','ID B2O','Тариф','Статус','Цена','Скидка','Итого','Действия'],c.services.map(x=>{const check=el('input','camera-check');check.type='checkbox';check.setAttribute('aria-label','Выбрать '+x.camera_name);check.checked=(S.bulkSelected||[]).includes(x.id);check.disabled=preview||x.id!==x.camera_id;check.onchange=()=>{S.bulkSelected=[...(S.bulkSelected||[]).filter(id=>id!==x.id),...(check.checked?[x.id]:[])];$('bulk-count').textContent='Выбрано: '+S.bulkSelected.length};return [check,x.camera_name,x.camera_id,x.camera_vc_id,x.tariff_name,x.status,x.price_text,x.discount_text,x.total_text,add(el('div','actionbar'),live('Кадр',()=>run(async()=>{S.billingImage=await op('billing_camera_image',{camera:x.id})})),live('История',()=>run(async()=>{S.history=await op('billing_history',{camera:x.id})})))]})));const count=el('span','muted','Выбрано: '+(S.bulkSelected||[]).length);count.id='bulk-count';root.append(add(el('div','actionbar'),count,live('Выбрать все',()=>{S.bulkSelected=c.services.filter(x=>x.id===x.camera_id).map(x=>x.id).slice(0,100);render(false)}),live('Снять выбор',()=>{S.bulkSelected=[];render(false)})));imagePreview(root,S.billingImage);
   action(root,live('Тарифы клиента',()=>run(async()=>{S.catalogFilter={};S.catalog=await op('billing_catalog',{scope:'client'})})));
   if(c.services.length){const cam=field('Камера','select','',c.services.map(x=>[x.id,x.camera_name+' · '+x.id])),a=field('Изменение','select','tariff',[['tariff','Сменить тариф'],['status','Изменить статус']]),t=field('Тариф','select','',(S.catalog?.tariffs||[]).filter(x=>x.country===c.country).map(x=>[x.id,x.name+' · '+x.price])),st=field('Статус','select','1',[[1,'Включить'],[0,'Отключить']]),date=field('Дата','date'),comment=field('Комментарий','text');form(root,cam,a,t,st,date,comment);action(root,live('Подготовить изменение',()=>stage('billing_preview',{camera:value(cam),action:value(a),tariff:value(t),status:num(st),date:value(date),comment:value(comment)}),'primary'));action(root,live('Сменить тариф выбранным',()=>stage('billing_bulk_preview',{cameras:S.bulkSelected||[],tariff:value(t),date:value(date),comment:value(comment)})))}
  }
  if(c?.services.length){const recipient=field('Логин получателя','text',S.recipientQuery||'');form(root,recipient);action(root,live('Найти получателя CRM',()=>run(async()=>{S.recipientQuery=value(recipient);S.recipients=await op('billing_recipient',{query:S.recipientQuery})})));if(S.recipients?.length){const to=field('Получатель','select','',S.recipients.map(r=>[r.id,r.login+' · '+r.country+' · '+r.account_name])),camera=field('Камера для переноса','select','',c.services.map(x=>[x.id,x.camera_name+' · '+x.id]));form(root,to,camera);action(root,live('Подготовить перенос',()=>stage('billing_transfer_preview',{recipient:value(to),camera:value(camera)}))) }}
  if(S.catalog){
   section(root,'Каталог тарифов');const cat=S.catalog,f=S.catalogFilter||{},country=field('Страна','select',f.country||'',[['','Все'],...(cat.countries||[]).map(x=>[x.name,x.name])]),type=field('Тип','select',f.account_type||'',[['','Все'],...(cat.account_types||[]).map(x=>[x,x])]),q=field('Название / ID','text',f.query||''),days=field('Глубина архива, дней (0 — все)','number',f.days||0),rate=field('Битрейт, Мбит/с (0 — все)','number',f.bitrate||0);
   days.input.min=0;rate.input.min=0;rate.input.step='.1';country.input.disabled=cat.scope==='client';form(root,country,type,q,days,rate);
   action(root,live('Применить фильтры',()=>run(async()=>{S.catalogFilter={country:value(country),account_type:value(type),query:value(q),days:num(days),bitrate:num(rate)};S.catalog=await op('billing_filter',S.catalogFilter)}),'primary'));
   action(root,live('Сбросить фильтры',()=>run(async()=>{S.catalogFilter={};S.catalog=await op('billing_filter',{})})));
   root.append(notice('Показано '+cat.tariffs.length+' из '+cat.total+' тарифов. Срок архива извлекается из названия, а не из тестового периода.'));
   root.append(table(['ID','Название','Страна','Тип','Архив, дн.','Мбит/с','Цена','Пробный период','Описание'],cat.tariffs.map(t=>[t.id,t.name,t.country,t.account_type,t.archive_days,t.bitrate_mbps,t.price+' '+t.currency,t.test_days,t.description])));
  }
  if(S.history){section(root,'История камеры');root.append(table(['Дата','Пользователь','Действие','Поле','До','После'],S.history.map(h=>[h.date,h.user,h.action,h.field_name,h.old_value,h.new_value])))}
  previewNotice(root,currentPlan(),out=>{if(out?.items){S.billingClient=out.client;S.bulkResult=out.items}else S.billingClient=out});if(S.bulkResult){section(root,'Результаты массового изменения');const labels={confirmed:'Подтверждено',unknown:'Неизвестный результат',not_confirmed:'Не подтверждено',not_attempted:'Не отправлено'};root.append(table(['Камера','Результат'],S.bulkResult.map(x=>[x.camera,labels[x.status]||x.status])))}resultNote(root);
 }
 function archive(root,diagnose){
  root.append(hero(diagnose?'ARCHIVE / DIAGNOSIS':'ARCHIVE / DOWNLOAD',diagnose?'Диагностика архива':'Архивы','Период задаётся в локальном времени камеры. Источник — существующий DVR / B2O сервис.'));
  sessionStrip(root,'b2o');
  const saved=S.archiveForm||{},cam=field('ID камеры','text',saved.camera||''),country=field('Регион','select',saved.operator||241,[[241,'Россия'],[1721,'Казахстан'],[1741,'Беларусь'],[1442,'Здравсервис / Goodline'],[621,'Провзор']]),start=field('Начало','datetime-local',saved.start||''),end=field('Конец','datetime-local',saved.end||''),compare=field('Камеры того же адреса, через запятую','text',saved.compare||''),server=field('Камеры vcore (необязательно, иначе автоподбор)','text',saved.server_compare||'');
  form(root,cam,country,start,end,...(diagnose?[compare,server]:[]));
  action(root,live(diagnose?'Выполнить диагностику':'Найти архив',()=>run(async()=>{S.archive=null;S.archiveSaved=null;S.archivePage=page;S.archiveForm={camera:value(cam),operator:num(country),start:value(start),end:value(end),compare:value(compare),server_compare:value(server)};const a={...S.archiveForm,compare:value(compare).split(',').map(s=>s.trim()).filter(Boolean),server_compare:value(server).split(',').map(s=>s.trim()).filter(Boolean)};S.archive=await op(diagnose?'archive_diagnose':'archive_discover',a)}),'primary'));
  const d=S.archive;if(d&&S.archivePage===page){const time=t=>new Date((t+d.timezone*3600)*1000).toISOString().slice(0,19).replace('T',' ');
   keyValue(root,[['Камера',d.camera],['Сервер',d.server],['Часовой пояс','UTC '+d.timezone],['Покрытие',Math.round(d.coverage*10)/10+'%'],['Пропуски',d.gaps.length],['Проверено серверов',d.checked],['Ошибки',d.error_count]]);
   if(!d.exact_timeline)root.append(notice('Точный DVR timeline не подтверждён; HLS не доказывает отсутствие разрывов.'));
   if(d.exact_timeline&&d.requested_end>d.requested_start){const box=el('div','panel timeline-box'),svg=document.createElementNS('http://www.w3.org/2000/svg','svg');svg.setAttribute('viewBox','0 0 1000 38');svg.setAttribute('preserveAspectRatio','none');svg.setAttribute('role','img');svg.setAttribute('aria-label','Интервалы архива за выбранный период');const draw=(start,end,cls)=>{const n=document.createElementNS(svg.namespaceURI,'rect'),range=d.requested_end-d.requested_start,x=Math.max(0,(start-d.requested_start)/range*1000),w=Math.min(1000-x,(end-start)/range*1000);if(w<=0)return;n.setAttribute('x',x);n.setAttribute('y','6');n.setAttribute('width',Math.max(.7,w));n.setAttribute('height','26');n.setAttribute('class',cls);const title=document.createElementNS(svg.namespaceURI,'title');title.textContent=time(start)+' — '+time(end);n.append(title);svg.append(n)};draw(d.requested_start,d.requested_end,'timeline-base');for(const s of d.slices)draw(s.start,s.end,'timeline-video');for(const g of d.gaps)draw(g.start,g.end,'timeline-gap');box.append(el('h3','','Лента записи'),svg,add(el('div','timeline-axis'),el('span','',time(d.requested_start)),el('span','',time(d.requested_end))),add(el('div','timeline-legend'),el('span','','Запись'),el('span','','Разрыв')));root.append(box)}
   if(d.gaps.length){section(root,'Пропуски');root.append(table(['Начало','Конец'],d.gaps.map(g=>[time(g.start),time(g.end)])))}
   if(d.diagnosis){section(root,d.diagnosis.title);root.append(add(el('div','panel'),el('h3','',d.diagnosis.side+' · '+d.diagnosis.confidence),el('p','',d.diagnosis.summary),...d.diagnosis.cautions.map(c=>el('p','',c))));if(d.compared)keyValue(root,[['Камер адреса',d.compared.address],['Камер vcore',d.compared.server],['Ошибки сравнения',d.compared.failed]]);if(d.compared?.auto_error)root.append(notice('Автоматический подбор камер vcore недоступен. Вывод ограничен имеющимися данными.'));if(d.diagnosis.gaps?.length)root.append(table(['Начало','Конец','Вывод'],d.diagnosis.gaps.map(g=>[time(g.start),time(g.end),g.title])))}
   if(!diagnose&&d.downloadable){
    const method=field('Метод скачивания','select',S.downloadMethod||'ffmpeg',[['ffmpeg','FFmpeg со звуком'],['ffmpeg_no_audio','FFmpeg без звука'],['curl','Прямой MP4 / curl']]);
    const manual=field('Ручная команда FFmpeg (необязательно)','textarea',S.manualCommand||'');
    manual.input.placeholder='ffmpeg -y -nostdin -hide_banner -loglevel error -progress pipe:1 -rw_timeout 30000000 -i "{input}" -c copy "{output}"';
    form(root,method,manual);
    const command=audio=>'ffmpeg -y -nostdin -hide_banner -loglevel error -progress pipe:1 -rw_timeout 30000000 -i "{input}" '+(audio?'-c copy':'-c:v copy -an')+' "{output}"';
    action(root,button('Шаблон со звуком',()=>{method.input.value='ffmpeg';manual.input.value=command(true)}));
    action(root,button('Шаблон без звука',()=>{method.input.value='ffmpeg_no_audio';manual.input.value=command(false)}));
    root.append(notice('Оставьте поле пустым для стандартной команды. В ручном режиме доступен ограниченный набор параметров; реальные URL и пути подставляет ядро.'));
    action(root,live(S.archiveSaved?'Скачать повторно':'Сохранить MP4',()=>run(async()=>{S.downloadMethod=value(method);S.manualCommand=value(manual);S.archiveSaved=await rpc('archiveDownload',S.downloadMethod,S.manualCommand);toast((S.archiveSaved.partial?'Частично сохранён: ':'Сохранён: ')+S.archiveSaved.output)}),'primary'));
    if(S.archiveSaved){const s=S.archiveSaved;keyValue(root,[['Файл',s.output],['Сохранено, сек.',s.downloaded_seconds],['Пропущено, сек.',s.failed_seconds],['Ошибок',s.error_count]]);action(root,live('Открыть папку',()=>run(()=>rpc('archiveLocation'))));action(root,live('Копировать путь',()=>rpc('copy',s.output).then(()=>toast('Путь скопирован')).catch(error)))}
   }
  }
 }
 function agent(root){
  root.append(hero('CAMERA / AGENT','Новый Агент','Поиск камеры и настройки времени, ориентации, битрейта через исходный AgentService.'));
  const sn=field('Серийный номер','text',S.serial||'');form(root,sn);
  action(root,live('Найти камеру',()=>run(async()=>{S.serial=value(sn);S.agentImage=null;S.agentSettings=null;S.agent=await op('agent_search',{serial:S.serial});S.plan=null}),'primary'));
  const c=S.agent;if(c){keyValue(root,[['SN',c.serial_number],['Модель',c.model],['Производитель',c.vendor],['Сервер',c.server],['Версия агента',c.version]]);
   action(root,live('Превью B2O',()=>run(async()=>{S.agentImage=await op('agent_image')})));imagePreview(root,S.agentImage);action(root,live('Прочитать настройки',()=>run(async()=>{S.agentSettings=await op('agent_settings')})));if(S.agentSettings)keyValue(root,[['Ориентация', ['Обычная','Вертикальное отражение','Горизонтальное отражение','180°'][S.agentSettings.rotation]],['Битрейт, Кбит/с',S.agentSettings.bitrate]]);const a=field('Настройка','select','timezone',[['timezone','Время / NTP'],['rotation','Ориентация изображения'],['bitrate','Битрейт (Кбит/с)']]),v=field('Часовой пояс / битрейт','number',7),rot=field('Ориентация','select',S.agentSettings?.rotation??0,[[0,'Обычная'],[1,'Вертикальное отражение'],[2,'Горизонтальное отражение'],[3,'Поворот 180°']]);form(root,a,v,rot);const switchField=()=>{rot.node.hidden=value(a)!=='rotation';v.node.hidden=value(a)==='rotation';v.input.value=value(a)==='bitrate'?(S.agentSettings?.bitrate||2048):7};a.input.onchange=switchField;switchField();action(root,live('Подготовить изменение',()=>stage('agent_preview',{action:value(a),value:value(a)==='rotation'?num(rot):num(v)}),'primary'))}
  previewNotice(root,currentPlan());resultNote(root);
 }
 function templates(root){
  root.append(hero('SUPPORT / KNOWLEDGE','Шаблоны ответов','Личная библиотека быстрых ответов — локальная SQLite база 4.0.'));
  const q=field('Поиск','text',S.templateQuery||''),cat=field('Категория','select',S.templateCategory||'',[['','Все'],...(S.templates?.categories||[]).map(v=>[v,v])]),fav=field('Избранное','select',S.templateFavorites?'yes':'no',[['no','Все шаблоны'],['yes','Только избранные']]);form(root,q,cat,fav);
  const load=async()=>{S.templateQuery=value(q);S.templateCategory=value(cat);S.templateFavorites=value(fav)==='yes';S.templates=await op('templates_list',{query:S.templateQuery,category:S.templateCategory,favorites:S.templateFavorites})};
  action(root,live('Загрузить шаблоны',()=>run(load),'primary'));action(root,live('Новый шаблон',()=>{S.editTemplate={};render(false)}));
  action(root,live('Импорт DOCX',()=>run(async()=>{const r=await rpc('fileAction','templates_import',{category:value(cat)});await load();toast('Импортировано: '+r.count)})));action(root,live('Перенести библиотеку 3.x',()=>run(async()=>{S.migrateResult=await rpc('fileAction','templates_migrate');await load()})));if(S.migrateResult)keyValue(root,[['Перенесено шаблонов',S.migrateResult.imported],['Уже имеются',S.migrateResult.skipped],['Изображений',S.migrateResult.images],['Недоступные вложения',S.migrateResult.missing]]);
  if(S.templates)for(const t of S.templates.items){const row=el('article','panel template');row.append(el('h3','',t.title),el('small','',t.category+(t.favorite?' · ★':'')),el('p','',t.body),add(el('div','actionbar'),live('Копировать ответ',()=>rpc('templateCopy',t.id).then(x=>toast(x.count?'Текст и '+x.count+' изображений в буфере'+(x.skipped?' · пропущено: '+x.skipped:''):'Текст скопирован')).catch(error)),live('Редактировать',()=>{S.editTemplate=t;render(false)}),live('Изображения',()=>run(async()=>{S.imageTemplate=t.id;S.images=await op('templates_images',{id:t.id})})),live('Прикрепить',()=>run(async()=>{await rpc('fileAction','templates_attach',{id:t.id});S.imageTemplate=t.id;S.images=await op('templates_images',{id:t.id})})),live(t.favorite?'Убрать из избранного':'В избранное',()=>run(async()=>{await op('templates_favorite',{id:t.id,selected:!t.favorite});await load()})),live('Удалить',()=>{if(confirm('Удалить локальный шаблон?'))run(async()=>{await op('templates_delete',{id:t.id});S.editTemplate=null;await load()})})));root.append(row)}
  if(S.imageTemplate){section(root,'Изображения шаблона');for(const img of S.images||[]){const im=el('img','template-image');if(/^data:image\/(png|jpeg|webp|gif);base64,/.test(img.src))im.src=img.src;im.alt='Вложение';root.append(add(el('div','panel'),im,live('Копировать фото',()=>rpc('templateCopy',S.imageTemplate,img.id).then(()=>toast('Фото скопировано')).catch(error)),live('Открепить',()=>{if(confirm('Убрать изображение из шаблона?'))run(async()=>{await op('templates_detach',{id:S.imageTemplate,image:img.id});S.images=await op('templates_images',{id:S.imageTemplate})})})))}}
  if(S.editTemplate){const t=S.editTemplate,title=field('Название','text',t.title||''),category=field('Категория','text',t.category||''),body=field('Текст','textarea',t.body||'');section(root,t.id?'Редактирование':'Новый шаблон');form(root,title,category,body);action(root,live('Сохранить',()=>run(async()=>{await op('templates_save',{id:t.id||null,title:value(title),category:value(category),body:value(body)});S.editTemplate=null;await load()}),'primary'))}
 }
 function recovery(root){
  root.append(hero('VPN / RECOVERY','Резерв и восстановление','FULL backup хранится локально в приватном профиле. Сценарии восстановления используют прежний аварийный источник Sheets.'));
  if(!preview&&identity?.role!=='sysadmin'){root.append(notice('Доступно системному администратору.'));return}
  if(!ensureHosts(root))return;const h=serverField();form(root,h);
  action(root,live('FULL backup сервера',()=>run(async()=>{S.backup=await op('backup',{host:setHost(h)})}),'primary'));
  action(root,live('FULL backup всех',()=>run(async()=>{S.backup=await op('backup',{all:true})})));
  if(S.backup){keyValue(root,[['Успешно',S.backup.success],['Ошибки',S.backup.failed],['Папка',S.backup.folder]]);root.append(table(['Сервер','Результат'],S.backup.servers.map(r=>[r.server,r.ok?'Подтверждён':'Ошибка'])))}
  section(root,'Google Sheets · Аварийное восстановление');action(root,live('Выбрать JSON-ключ',()=>run(async()=>{S.sheets=await rpc('fileAction','sheets_load')})));
  if(S.sheets?.configured)root.append(notice('Ключ принят в память текущего процесса.'));
  const q=field('Удалённый логин','text',S.deletedQuery||'');form(root,q);
  action(root,live('Искать удалённые',()=>run(async()=>{S.deletedQuery=value(q);S.deleted=await op('deleted_search',{query:S.deletedQuery})}),'primary'));
  action(root,live('Подготовить экспорт сервера в Sheets',()=>stage('sheets_preview',{host:setHost(h)})));action(root,live('Подготовить аварийный экспорт всех',()=>stage('sheets_preview',{all:true})));
  if(S.deleted)root.append(table(['Логин','Сервер','VPN IP','Удалён','Порты',''],S.deleted.map(r=>[r.login,r.server,r.remote_address,r.deleted_at,r.ports,live('Восстановить',()=>stage('restore_preview',{host:r.server,login:r.login}))])));
  previewNotice(root,currentPlan(),out=>{if(out?.sheets_results)S.sheetsResult=out});if(S.sheetsResult){section(root,'Результаты аварийного экспорта');root.append(table(['Сервер','Подтверждение','Клиентов','Изменено'],S.sheetsResult.sheets_results.map(x=>[x.server,x.success?'Подтверждено':'Не подтверждено',x.clients,x.changed])));if(S.sheetsResult.cancelled)root.append(notice('Экспорт остановлен; уже подтверждённые записи не отменяются.'))}resultNote(root);
 }
 function cloudSettings(root){
  section(root,'Облачный сервер · VPNSync');
  const card=el('div','panel');card.append(el('p','muted','Отдельная учётная запись API для PostgreSQL, архива и аудита. Поиск активных VPN по-прежнему обращается непосредственно к MikroTik.'));root.append(card);
  const c=S.cloud?.connected?S.cloud:S.cloudProfile||{},host=field('DNS или IPv4','text',c.host||''),port=field('Порт','number',c.port||443),u=field('Логин VPNSync','text',c.username||''),pw=field('Пароль VPNSync','password'),scheme=field('Протокол','select',c.tls===false?'http':'https',[['https','HTTPS'],['http','HTTP в доверенной сети']]),remember=field('Запомнить','select',c.remembered?'yes':'no',[['no','Только этот сеанс'],['yes','Шифрование Windows']]);form(root,host,port,u,pw,scheme,remember);
  action(root,live('Подключить и проверить',()=>{const password=pw.input.value;pw.input.value='';run(async()=>{S.cloud=await rpc('cloudLogin',{host:value(host),port:num(port),username:value(u),password,tls:value(scheme)==='https',remember:value(remember)==='yes'});S.cloudProfile=null})},'primary'));
  action(root,live('Показать сохранённое подключение',()=>run(async()=>{S.cloudProfile=await rpc('cloudProfile');if(!S.cloudProfile)toast('Нет сохранённого подключения')})));
  action(root,live('Подключить сохранённое',()=>run(async()=>{S.cloud=await rpc('cloudSaved')})));
  action(root,live('Отключить и забыть',()=>run(async()=>{S.cloud=await rpc('cloudDisconnect');S.cloudProfile=null;S.clientHistory=null})));
  if(S.cloud?.connected)root.append(notice('VPNSync подключён · '+S.cloud.host+':'+S.cloud.port));
 }
 function clientHistory(root,c){
  if(identity?.role!=='sysadmin')return;
  section(root,'История VPN · '+c.login);
  const source=field('Источник','select',S.clientHistorySource||'',[['','Все'],['desktop','Helper'],['routeros','MikroTik'],['archive','Восстановление'],['retention','Автоматика']]);form(root,source);
  action(root,live('Загрузить историю клиента',()=>run(async()=>{S.clientHistorySource=value(source);S.clientHistory=await op('activity',{host:c.server,login:c.login,source:S.clientHistorySource})}),'primary'));
  if(S.clientHistory){const actions={'client.create':'Создан VPN','nat.add_ports':'Добавлены порты','nat.remove_port':'Удалён порт','nat.recreate':'Пересоздан порт','client.password_change':'Пароль изменён','client.enabled_change':'Состояние VPN','nat.enabled_change':'Состояние NAT','client.disconnect':'Разрыв сессии','client.delete':'Удалён VPN','archive.restore':'Восстановление VPN','retention.sleep':'Спящий статус','retention.quarantine':'Карантин','retention.delete':'Автоматическое удаление','retention.error':'Ошибка автоматики'};const labels={count:'Количество',port:'Порт',enabled:'Включено',ports_per_client:'Портов на учётку',accounts_count:'Учёток',clients:'Клиентов',active:'Активных',added:'Добавлено',changed:'Изменено',deleted:'Удалено',restored_objects:'Восстановлено',session_removed:'Сессия удалена',active_sessions:'Сессий'};
   root.append(table(['Время','Событие','Источник','Оператор','Подробности','Результат'],S.clientHistory.map(r=>[r.created_at||r.timestamp,actions[r.action]||r.action,r.source,r.actor||r.username,Object.entries(r.details||{}).map(([k,v])=>(labels[k]||k)+': '+(typeof v==='boolean'?(v?'Да':'Нет'):v)).join(' · '),r.success])));
  }
 }
 function camera(root){
  root.append(hero('B2O / CAMERAS','Камеры и клиенты B2O','Проверка RTSP/TCP, сведения об устройстве, чтение и создание корпоративной учётки.'));
  const query=field('Логин клиента B2O','text',S.cameraLookupQuery||'');form(root,query);action(root,live('Найти камеры клиента',()=>run(async()=>{S.cameraLookupQuery=value(query);S.cameraChoices=await op('camera_lookup',{query:S.cameraLookupQuery})}),'primary'));
  if(S.cameraChoices){root.append(notice('Найдено камер с подтверждённым логином: '+S.cameraChoices.length));root.append(table(['ID','Название','Регион','Статус',''],S.cameraChoices.map(c=>[c.camera_id,c.label,c.country,c.status,live('Выбрать',()=>{S.cameraId=c.camera_id;S.cameraOperator=c.operator_id;S.cameraInfo=null;S.cameraLive=null;render(false)})])))}
  const id=field('ID камеры','text',S.cameraId||''),opid=field('Регион','select',S.cameraOperator||241,[[241,'Россия'],[1721,'Казахстан'],[1741,'Беларусь'],[1442,'Здравсервис / Goodline'],[621,'Провзор']]);form(root,id,opid);
  action(root,live('Диагностика камеры',()=>run(async()=>{S.cameraId=value(id);S.cameraOperator=num(opid);S.cameraLive=null;S.cameraInfo=await op('camera_inspect',{camera:S.cameraId,operator:S.cameraOperator})}),'primary'));
  const c=S.cameraInfo;if(c)keyValue(root,[['Камера',c.name||c.camera_id],['Сервер',c.server],['IP',c.ip],['RTSP-порт',c.rtsp_port],['Ping',c.ping_text],['TCP',c.tcp_text],['Состояние',c.status],['Модель',c.model],['SN',c.serial],['Архив',c.archive_depth],['Последний поток',c.last_stream]]);
  if(c){action(root,live('Измерить поток',()=>run(async()=>{S.cameraLive=await op('camera_live')}),'primary'));if(S.cameraLive){const x=S.cameraLive;keyValue(root,[['Битрейт, Мбит/с',x.bitrate_mbps],['Видео',x.video_codec],['Аудио',x.audio_codec],['Разрешение',x.resolution],['FPS',x.fps],['Источник',x.source],['Последний поток',x.last_stream]]);root.append(notice(x.connectivity),notice(x.detail))}}
  section(root,'Клиент B2O');const country=field('Страна','select','Россия',[['Россия','Россия'],['Казахстан','Казахстан'],['Беларусь','Беларусь']]),account=field('ID аккаунта','number');form(root,country,account);
  action(root,live('Проверить клиента',()=>run(async()=>{S.b2oClient=await op('b2o_read',{country:value(country),id:num(account)})})));
  if(S.b2oClient)keyValue(root,[['ID',S.b2oClient.id_account],['Логин',S.b2oClient.login],['Имя',S.b2oClient.client_name],['Тип',S.b2oClient.account_type]]);
  section(root,'Создание обычного аккаунта с биллингом');const name=field('Имя клиента'),login=field('Логин'),password=field('Пароль','password'),phone=field('Телефон');form(root,name,login,password,phone);action(root,live('Подготовить создание',()=>{const pw=password.input.value;password.input.value='';stage('b2o_create_preview',{country:value(country),name:value(name),login:value(login),password:pw,phone:value(phone)})},'primary'));
  previewNotice(root,currentPlan());if(S.outcome?.id_account&&S.planPage===page)keyValue(root,[['Создан ID',S.outcome.id_account],['Проверка чтением',S.outcome.read_back_verified?'Да':'Не подтверждена']]);resultNote(root);
 }
 function dashboard(root){
  section(root,'Рабочие инструменты');const g=el('div','dashboard-grid');
  for(const [id,desc] of [['create','Создание учёток и NAT'],['inactive','Жизненный цикл VPN'],['lv','Скрипты и автоматизация'],['recovery','FULL backup · восстановление'],['billing','Тарифы и услуги'],['archives','Поиск и экспорт MP4'],['diagnostics','Разрывы и сравнение'],['camera','B2O и сетевые проверки'],['agent','Настройки камеры'],['templates','Библиотека ответов'],['admin','Независимые сессии']]){
   const p=pages.find(x=>x[0]===id);if(!p||!pageAllowed(id))continue;const b=btn('','dash-tile',()=>go(id));b.append(navIcon(id,p[1]),add(el('div'),el('strong','',p[2]),el('small','',desc)),el('span','dash-arrow','›'));g.append(b)
  }
  root.append(g);
 }
 function workspaceSettings(root){
  if(preview){root.append(notice('Настройки корпоративных серверов доступны после входа.'));return}
  section(root,'Защищённые учётные данные');action(root,live('Очистить сохранённый вход',()=>run(()=>rpc('clearSaved','work'))));cloudSettings(root);
  section(root,'Архив · кластеры и серверы');
  action(root,live('Настройки архива',()=>run(async()=>{S.archiveSettings=await op('archive_settings')}),'primary'));
  if(S.archiveSettings){
   for(const item of S.archiveSettings.clusters){const b=el('div','panel setting-row');const sw=btn('','switch',()=>run(async()=>{S.archiveSettings=await op('archive_settings_update',{action:'cluster',cluster:item.key,enabled:!item.enabled})}));sw.setAttribute('role','switch');sw.setAttribute('aria-label',item.name);sw.setAttribute('aria-checked',String(item.enabled));sw.append(el('span'));b.append(add(el('div'),el('h3','',item.name),el('p','','Operator '+item.operator)),sw);root.append(b)}
   if(identity?.role==='sysadmin'){for(const group of S.archiveSettings.hosts){if(!group.custom.length)continue;section(root,group.country+' · '+group.operator);root.append(table(['Сервер',''],group.custom.map(host=>[host,live('Удалить',()=>run(async()=>{S.archiveSettings=await op('archive_settings_update',{action:'remove_host',operator:group.operator,host})}))])))}const operator=field('Оператор','select','241',S.archiveSettings.hosts.map(x=>[x.operator,x.country+' · '+x.operator])),host=field('Дополнительный архивный сервер','text');form(root,operator,host);action(root,live('Добавить сервер архива',()=>run(async()=>{S.archiveSettings=await op('archive_settings_update',{action:'add_host',operator:num(operator),host:value(host)})})))}
  }
  section(root,'Журнал работы Helper 4');const logCard=el('div','panel');logCard.append(el('p','muted','Локальный журнал операций: время, название, результат и длительность. Без параметров, паролей, токенов и содержимого буфера обмена.'));
  const loadLogs=async()=>{S.logs=await rpc('logRead')};
  logCard.append(add(el('div','actionbar'),live('Обновить',()=>run(loadLogs),'primary'),live('Открыть папку',()=>run(()=>rpc('logFolder'))),live('Очистить',()=>run(async()=>{await rpc('logClear');await loadLogs()})),live('Скопировать',()=>rpc('copy',(S.logs||[]).map(x=>x.time+' '+x.action+' '+(x.ok?'OK':'ERROR')+' '+x.ms+' ms').join('\n')).then(()=>toast('Журнал скопирован')).catch(error))));
  if(S.logs){const out=el('pre','runtime-log',(S.logs||[]).map(x=>x.time+'  '+(x.ok?'OK   ':'ERROR')+'  '+x.action+'  '+x.ms+' ms').join('\n')||'Журнал пуст.');out.tabIndex=0;logCard.append(out)}root.append(logCard);
  if(identity?.role!=='sysadmin')return;
  section(root,'Реестр VPN-серверов');
  const load=async()=>{S.registry=await op('registry_list');S.hosts=await rpc('servers');serverState=null;searchState=null};
  action(root,live('Открыть реестр',()=>run(load),'primary'));
  if(S.registry){root.append(table(['Адрес','Регион','Доступ',''],S.registry.map(r=>[r.host,r.country,r.enabled?(r.trusted?'Активен':'Требуется доверие'):'Выключен',add(el('div','actionbar'),...(r.enabled&&r.trusted?[live('Проверить',()=>run(async()=>{S.registryTest=await rpc('analyze',r.host)}))]:[]),live(r.enabled?'Выключить':'Включить',()=>run(async()=>{await op('registry_update',{host:r.host,action:r.enabled?'disable':'enable'});await load()})),...(!r.builtin?[live('Доверять',()=>run(async()=>{await op('registry_update',{host:r.host,action:'trust',country:r.country});await load()})),live('Удалить',()=>run(async()=>{await op('registry_update',{host:r.host,action:'remove'});await load()}))]:[]))])));
   const h=field('Дополнительный DNS/IPv4','text'),c=field('Регион','text');form(root,h,c);action(root,live('Добавить доверенный сервер',()=>run(async()=>{await op('registry_update',{host:value(h),country:value(c),action:'trust'});await load()})))
  }
  if(S.registryTest)keyValue(root,[['Сервер отвечает',S.registryTest.server],['CPU',S.registryTest.cpu_load],['RAM',S.registryTest.memory_usage_percent],['VPN онлайн',S.registryTest.clients_online]]);
  section(root,'Фоновая служба Windows 3.x');
  action(root,live('Проверить существующее задание',()=>run(async()=>{S.background=await op('background_status')})));action(root,live('Открыть планировщик Windows',()=>run(()=>rpc('taskScheduler'))));
  if(S.background){const b=S.background;keyValue(root,[['Задание',b.verified?'Обнаружено':b.installed?'Есть квитанция 3.x, не проверено':'Не подтверждено'],['Период, ч',b.interval_hours],['Статус',b.status],['Последняя проверка UTC',b.checked_utc],['Успешно',b.successful],['Ошибок',b.failed]])}
  root.append(notice('Google Sheets — ручной аварийный резерв, PostgreSQL — основная база. Задание LinkVideo.Helper.VPNSheetsMirror можно проверить в планировщике. 4.0 не создаёт, не переустанавливает и не удаляет SYSTEM-задачу 3.x.'))
 }
 window.lvModules=Object.freeze({reset,client,stopTraffic,cloudRestore:c=>stage("cloud_restore_preview",{host:c.server,login:c.login}),dashboard,settings:workspaceSettings,clearClient:()=>{stopTraffic();S.clientHistory=null;S.camPorts=null;S.client=null;S.plan=null;S.outcome=null},renderClient,render:(name,root)=>{({create,inactive,lv,recovery,admin,billing,archives:r=>archive(r,false),diagnostics:r=>archive(r,true),agent,camera,templates})[name]?.(root)}});
})();

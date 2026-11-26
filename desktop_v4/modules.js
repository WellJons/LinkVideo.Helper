'use strict';
(()=>{
 let S={};
 const reset=()=>{stopTraffic();stopRefresh();stopThumbs();S={}};
 const discardPlan=()=>{S.planGeneration=(S.planGeneration||0)+1;S.plan=null;S.outcome=null;S.planPage=null};
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
 const run=async(fn)=>{if(taskBusy){toast('Дождитесь завершения текущей операции.');return}const owner=identity,route=epoch;try{return await fn()}catch(err){if(identity===owner)error(err)}finally{if(identity===owner&&owner&&epoch===route)render(false)}};
 const keyValue=(root,entries)=>{const d=el('div','kv-grid');for(const [k,v] of entries)d.append(add(el('div','kv'),el('small','',k),el('strong','',v==null||v===''?'—':Array.isArray(v)?v.join(', '):String(v))));root.append(d)};
 const status=v=>el('span',v?'online':'offline',v?'В сети':'Не в сети');
 const serverField=(title='VPN-сервер')=>field(title,'select',S.host||'',(S.hosts||[]).map(r=>[r.host,r.host+' · '+r.country]));
 const ensureHosts=(root)=>{if(S.hosts?.length)return true;root.append(notice('Загрузите список доступных серверов.'));action(root,live('Загрузить серверы',()=>run(async()=>{S.hosts=await rpc('servers');S.host=S.hosts[0]?.host||''}),'primary'));return false};
 const setHost=f=>{S.host=value(f);return S.host};
 const previewNotice=(root,plan,onConfirm)=>{if(!plan)return;const p=el('div','panel confirmation');p.append(el('p','eyebrow','ПОДТВЕРЖДЕНИЕ'),el('h3','',plan.label),el('p','','Одноразовое разрешение действует 120 секунд. Проверьте параметры до отправки.'));const b=live('Подтвердить',()=>run(async()=>{const token=plan.token,owner=identity,from=epoch;S.plan=null;const out=await op('commit',{token});if(identity===owner&&epoch===from){S.outcome=out;if(onConfirm)onConfirm(out)}else if(identity===owner)toast('Операция завершена. Обновите прежний раздел для проверки результата.')}),'primary');p.append(add(el('div','actionbar'),b,button('Отменить',()=>{S.plan=null;render(false)})));root.append(p)};
 const stage=(method,args)=>{const from=page,generation=epoch,owner=identity;stopTraffic();return run(async()=>{discardPlan();const planGeneration=S.planGeneration;const plan=await op(method,args);if(epoch===generation&&identity===owner&&S.planGeneration===planGeneration){S.plan=plan;S.planPage=from}else toast('Раздел или параметры изменились. Подготовьте действие заново.')})};
 const currentPlan=()=>S.planPage===page?S.plan:null;
 const resultNote=(root)=>{if(S.outcome&&S.planPage===page)root.append(notice('Операция завершена. Проверьте актуальное состояние целевой записи.'))};
 let thumbJob=null;
 function stopThumbs(){if(thumbJob){clearTimeout(thumbJob.timer);thumbJob=null}}
 function thumbnailState(kind,source){
  const key=kind+'Thumbs';
  if(S[key]?.source!==source)S[key]={source,images:new Map(),attempted:new Set(),bytes:0};
  return S[key]
 }
 function showThumb(node,src){
  if(!node||typeof src!=='string'||!/^data:image\/(png|jpeg|webp);base64,/.test(src))return;
  const im=el('img');im.alt='Кадр камеры';im.decoding='async';im.onerror=()=>{if(im.parentNode===node)node.replaceChildren(el('span','muted','Нет превью'))};im.onload=()=>{if(im.isConnected&&motion&&!reduced.matches)im.animate([{opacity:.35},{opacity:1}],{duration:160,easing:'ease-out'})};node.replaceChildren(im);im.src=src
 }
 function queueThumbs(kind,source,targets,state){
  if(preview||!S.admin?.b2o||!targets.length)return;
  if(thumbJob?.state===state&&thumbJob.route===epoch)return;
  stopThumbs();
  const owner=identity,route=epoch;
  const job={state,timer:null,route};thumbJob=job;
  const valid=()=>thumbJob===job&&identity===owner&&epoch===route&&!preview&&!!S.admin?.b2o&&
    (kind==='billing'?page==='billing'&&(S.billingTab||'search')==='search'&&(S.billingCardTab||'services')==='services'&&S.billingClient===source:page==='camera'&&!S.cameraInfo&&S.cameraChoices===source);
  const next=ms=>{clearTimeout(job.timer);if(valid())job.timer=setTimeout(step,ms);else if(thumbJob===job)stopThumbs()};
  async function step(){
   if(!valid()){if(thumbJob===job)stopThumbs();return}
   if(taskBusy||idleRead||S.plan||document.hidden){next(500);return}
   const target=targets.find(x=>!state.attempted.has(x.key)&&!state.images.has(x.key));
   if(!target||state.bytes>=8*1024*1024){stopThumbs();return}
   try{
    const reply=await backgroundRead(target.method,target.args);
    if(!valid())return;
    if(!reply)return;
    state.attempted.add(target.key);
    const src=reply.ok?reply.data?.src:null;
    if(typeof src==='string'&&src.length<=1500000&&state.bytes+src.length<=8*1024*1024&&/^data:image\/(png|jpeg|webp);base64,/.test(src)){
     state.images.set(target.key,src);state.bytes+=src.length;
     const node=[...document.querySelectorAll('[data-thumb-key]')].find(x=>x.dataset.thumbKey===target.key);
     showThumb(node,src)
    }
   }catch{if(valid())state.attempted.add(target.key)}
   finally{if(valid())next(100);else if(thumbJob===job)stopThumbs()}
  }
  next(100)
 }
 function imagePreview(root,result){if(!result?.src||!/^data:image\/(png|jpeg|webp);base64,/.test(result.src))return;const i=el('img','camera-thumb');i.alt='Кадр камеры';i.src=result.src;root.append(add(el('div','panel camera-preview'),el('h3','','Превью камеры'),i))}
 async function loadCreateHosts(){
  const owner=identity;if(!owner||preview||S.createLoading)return;
  S.createLoading=true;if(page==='create')render(false);
  try{const hosts=await rpc('servers');if(identity===owner){S.hosts=hosts;S.createLoadError=null}}
  catch(err){if(identity===owner)S.createLoadError=err.message}
  finally{if(identity===owner){S.createLoading=false;if(page==='create')render(false)}}
 }
 function create(root){
  root.append(hero('VPN / PROVISIONING','Создание клиента','Новая VPN-учётная запись без лишних шагов. Выберите сервер, задайте логин и параметры.'));
  const hosts=S.hosts||[],countries=[...new Set(['Россия','Казахстан','Беларусь',...hosts.map(x=>x.country)])];
  S.createRegion=S.createRegion||'Россия';
  const available=hosts.filter(x=>x.country===S.createRegion);
  if(!available.some(x=>x.host===S.createHost))S.createHost=available[0]?.host||'';
  const region=field('Страна','select',S.createRegion,countries.map(x=>[x,x]));
  const h=field('VPN-сервер','select',S.createHost,available.length?available.map(x=>[x.host,x.host]):[['','Нет доступных серверов']]);
  const l=field('Логин клиента','text',S.createLogin||''),p=field('Порты','number',S.createPorts??3),c=field('Учётки','number',S.createCount??1);
  l.input.placeholder='Например, 890000001';l.input.maxLength=64;
  p.input.min=1;p.input.max=14;c.input.min=1;c.input.max=20;p.input.step=1;c.input.step=1;
  for(const [key,f] of [['region',region],['host',h],['login',l],['ports',p],['count',c]])f.input.dataset.createControl=key;
  const layout=el('div','create-workspace'),left=el('div','panel create-form'),right=el('div','panel create-result');
  const step=(n,title)=>add(el('h2','create-step'),el('span','create-step-number',n),el('span','',title));
  left.append(step('1','Выбор VPN-сервера'),region.node,h.node);
  h.input.disabled=!available.length||!!S.createLoading;
  if(S.createLoading)left.append(busy('Загружаю список серверов…'));
  else if(!hosts.length){left.append(notice(S.createLoadError||'Нет загруженных VPN-серверов.'));left.append(live('Загрузить серверы',loadCreateHosts))}
  else if(!available.length)left.append(notice('В выбранной стране нет доступного VPN-сервера. Другой регион не подставляется.'));
  left.append(step('2','Параметры клиента'));
  const check=button('Проверить',()=>run(async()=>{capture();invalidate();lock();S.createCheck=await op('create_check',{host:S.createHost,login:S.createLogin})}),'quiet',preview||!S.createHost);
  const loginRow=add(el('div','create-login-row'),l.node,check);left.append(loginRow);
  const checkBox=el('div','create-check');
  if(S.createCheck){const x=S.createCheck;checkBox.append(notice(x.login+' · '+x.host+' · '+({free:'Свободен на момент проверки',occupied:'Занят',unknown:'Проверка не завершена'})[x.status]));if(x.status==='occupied'&&x.suggestion)checkBox.append(button('Использовать '+x.suggestion,()=>{S.createLogin=x.suggestion;discardPlan();S.createCheck=null;render(false)}))}
  left.append(checkBox,add(el('div','create-counters'),p.node,c.node));
  const capture=()=>{S.createLogin=value(l);S.createPorts=num(p);S.createCount=num(c);S.createRegion=value(region);S.createHost=value(h)};
  const invalidate=()=>{discardPlan();S.createCheck=null;checkBox.replaceChildren();left.querySelector('.confirmation')?.remove()};
  const lock=()=>{for(const n of left.querySelectorAll('input,select,button'))n.disabled=true};
  l.input.oninput=()=>{capture();invalidate()};
  p.input.oninput=c.input.oninput=()=>{capture();invalidate()};
  region.input.onchange=()=>{capture();invalidate();S.createHost='';render(false)};
  h.input.onchange=()=>{capture();invalidate();render(false)};
  l.input.onkeydown=e=>{if(e.key==='Enter'&&!check.disabled){e.preventDefault();check.click()}};
  const prepare=auto=>run(async()=>{
   capture();if(!l.input.reportValidity()||!p.input.reportValidity()||!c.input.reportValidity()||!S.createLogin)throw Error('Заполните логин и корректное количество портов и учёток.');
   invalidate();S.createResult=null;lock();const owner=identity,route=epoch;
   if(auto){const picked=await op('create_auto',{country:S.createRegion});if(identity!==owner||epoch!==route)return;if(!available.some(x=>x.host===picked.host))throw Error('Подобранный сервер не относится к выбранному региону.');S.createHost=picked.host}
   const plan=await op('create_preview',{host:S.createHost,login:S.createLogin,ports:S.createPorts,count:S.createCount});
   if(identity===owner&&epoch===route){S.plan=plan;S.planPage='create'}
  });
  const blocked=preview||!S.createHost||!identity?.writes;
  const manual=button('Создать клиента',()=>prepare(false),'primary',blocked),auto=button('Подобрать сервер автоматически',()=>prepare(true),'quiet',blocked);
  manual.dataset.createAction='manual';auto.dataset.createAction='auto';left.append(add(el('div','actionbar'),manual,auto));
  if(!preview&&!identity?.writes)left.append(notice('Режим проверки: создание учёток отключено.'));
  previewNotice(left,currentPlan(),out=>{S.createResult=out?.clients||[]});
  right.append(el('h2','','Данные клиента'),el('p','muted','После создания здесь появятся сервер, логин, адрес и порты. Пароль передаётся только в системный буфер обмена.'));
  const rows=S.createResult||[];
  if(!rows.length)right.append(add(el('div','create-empty'),el('span','create-empty-symbol','◇'),el('h3','','Клиент ещё не создан'),el('p','muted','Заполните форму и нажмите «Создать клиента».')));
  else for(const row of rows){const card=el('div','create-client-card');card.append(el('h3','',row.login));keyValue(card,[['Сервер',row.server],['VPN IP',row.remote_address],['Порты',row.ports]]);card.append(live('Копировать VPN',()=>rpc('copyVpn',row.server,row.login).then(()=>toast('Скопировано')).catch(error)));right.append(card)}
  right.append(button('Копировать данные',()=>rpc('copyVpnBatch',rows.map(c=>({host:c.server,login:c.login}))).then(x=>toast('Скопировано: '+x.count)).catch(error),'quiet',preview||!rows.length));
  layout.append(left,right);root.append(layout);
 }
 async function client(host,login){
  if(S.client?.server===host&&S.client?.login===login)return refreshClient();
  stopTraffic();stopRefresh();S.clientHistory=null;S.camPorts=null;S.cameraLogin='';S.client=null;S.selectedPort=null;S.expandedPorts=new Set();S.cameraMatchError='';S.portCount=1;discardPlan();S.newPorts=[];S.refreshWarning='';
  const owner=identity,route=epoch;
  await run(async()=>{const data=await op('client_detail',{host,login});if(identity!==owner||epoch!==route||page!=='search')return;S.client=data;syncResult(data);render(true);startRefresh();if(S.admin?.b2o&&data.ports.length){try{const match=await op('camera_ports',{host,login,override:''});if(identity===owner&&epoch===route&&S.client?.server===host&&S.client?.login===login)S.camPorts=match}catch{if(identity===owner&&epoch===route)S.cameraMatchError='Автоматическая сверка камер не завершена. Повторите вручную.'}}});
 }
 let refresh=null;
 function stopRefresh(){if(refresh){refresh.stopped=true;clearTimeout(refresh.timer);refresh=null}}
 function syncResult(c,remove=false){const data=searchState?.data;if(!data?.matches)return;const i=data.matches.findIndex(x=>x.server===c.server&&x.login===c.login);if(i>=0){if(remove)data.matches.splice(i,1);else data.matches[i]={...data.matches[i],...c};const result=$('search-results');if(result)preserveScroll(()=>showMatches(result,searchState))}}
 function applyClientRefresh(now,full=false){
  const before=S.client;if(!before||before.server!==now.server||before.login!==now.login)return;
  const changed=JSON.stringify([before.ports,before.disabled_ports,before.remote_address])!==JSON.stringify([now.ports,now.disabled_ports,now.remote_address]);
  if(!full){now.conflicts_verified=!changed&&before.conflicts_verified===true;now.port_conflicts=now.conflicts_verified?before.port_conflicts:{}}
  S.client=now;S.newPorts=(S.newPorts||[]).filter(p=>now.ports.includes(p));S.refreshWarning='';if(!now.ports.includes(S.selectedPort))S.selectedPort=now.ports[0]??null;
  if(changed){S.camPorts=null;const box=$('client-camera-result');if(box)box.replaceChildren(notice('Порты изменились. Повторите сверку B2O.'))}
  syncResult(now);
 }
 function paintClient(){
  const panel=$('client-live-card');if(!panel||!S.client)return;
  preserveScroll(()=>{const active=document.activeElement,controls=[...panel.querySelectorAll('button')],i=controls.indexOf(active),label=active?.textContent;updateClientCard(panel,S.client);const b=panel.querySelectorAll('button')[i];if(b&&b.textContent===label&&!b.disabled)b.focus({preventScroll:true});const status=$('client-online');if(status){status.textContent=S.client.is_online?'В сети':'Не в сети';status.className=S.client.is_online?'online':'offline'}paintPortControls()});
 }
 async function refreshClient(){
  const c=S.client,owner=identity,route=epoch;if(!c||preview||taskBusy)return;
  stopRefresh();
  try{const data=await op('client_detail',{host:c.server,login:c.login});if(identity===owner&&epoch===route&&S.client===c)preserveScroll(()=>{applyClientRefresh(data,true);paintClient()})}
  catch(err){if(identity===owner&&epoch===route)error(err)}
  finally{if(identity===owner&&epoch===route&&page==='search'&&S.client)startRefresh()}
 }
 function invalidateClientPlan(){discardPlan();$('page').querySelector('.confirmation')?.remove()}
 function choosePort(p){
  if(!S.client?.ports.includes(p)||S.selectedPort===p)return;
  invalidateClientPlan();S.selectedPort=p;
  for(const node of document.querySelectorAll('[data-port-select]'))node.setAttribute('aria-pressed',String(Number(node.dataset.portSelect)===p));
  paintPortControls()
 }
 function paintPortControls(){
  const c=S.client;if(!c)return;
  if(!c.ports.includes(S.selectedPort))S.selectedPort=c.ports[0]??null;
  const p=S.selectedPort,writable=!preview&&!!identity?.writes&&identity?.role==='sysadmin',conflict=!!c.port_conflicts?.[p],disabled=c.disabled_ports.includes(p);
  const title=$('vpn-selected-port');if(title)title.textContent=p?(conflict?'Конфликт порта ':'Выбран порт ')+p:'Порт не выбран';
  for(const [id,label] of [['vpn-toggle-port',p?(disabled?'Включить порт ':'Отключить порт ')+p:'Выберите порт'],['vpn-remove-port',p?'Удалить порт '+p:'Удалить порт'],['vpn-recreate-port',p?'Пересоздать порт '+p:'Пересоздать порт']]){const n=$(id);if(n){n.textContent=label;n.disabled=!writable||!p||conflict}}
  const account=$('vpn-account-toggle');if(account){account.textContent=c.is_enabled?'Отключить учётку':'Включить учётку';account.disabled=!writable}
 }
 function updateClientCard(root,c){
  root.replaceChildren();
  section(root,'Данные клиента');
  keyValue(root,[['Сервер',c.server],['Логин',c.login],['VPN IP',c.remote_address],['IP источника',c.tunnel_source_ip]]);
  section(root,'Состояние VPN');
  keyValue(root,[['Статус',c.is_online?'В сети':'Не в сети'],['Учётка',c.is_enabled?'Включена':'Отключена'],['Uptime',c.uptime],['Последний выход',c.last_logged_out],['RX, байт',c.rx_bytes],['TX, байт',c.tx_bytes]]);
  const fresh=new Set(S.newPorts||[]);
  if(fresh.size){root.append(notice('Добавлены порты: '+[...fresh].join(', ')));action(root,live('Копировать новые порты',()=>rpc('copy',[...fresh].join(', ')).then(()=>toast('Новые порты скопированы')).catch(error)))}
  if(!c.ports.includes(S.selectedPort))S.selectedPort=c.ports[0]??null;
  if(!S.expandedPorts)S.expandedPorts=new Set();
  section(root,'Порты NAT · '+c.ports.length);
  const list=el('div','vpn-port-list'),links=new Map(),groups=new Map(S.camPorts?.grouped||[]);
  for(const link of S.camPorts?.links||[])links.set(Number(link.port),link);
  for(const p of c.ports){
   const conflict=c.port_conflicts?.[p],card=el('article','vpn-port-card'),selected=button('Порт '+p+(fresh.has(p)?' · новый':''),()=>choosePort(p),'quiet');selected.dataset.portSelect=String(p);selected.setAttribute('aria-pressed',String(S.selectedPort===p));
   card.append(add(el('div','vpn-port-head'),selected,el('span',c.disabled_ports.includes(p)?'muted':'online',c.disabled_ports.includes(p)?'Отключён':c.active_ports.includes(p)?'Активен':'Включён'),live('Копировать',()=>rpc('copy',String(p)).then(()=>toast('Порт скопирован')).catch(error))));
   card.append(el('p','muted','TCP · dst-nat · NAT-трафик: '+(c.port_nat_bytes?.[p]??'—')+' байт · Пакеты: '+(c.port_nat_packets?.[p]??'—')));
   if(conflict)card.append(notice('Конфликт внешнего порта: '+conflict));
   else if(c.conflicts_verified!==true)card.append(el('small','muted','Конфликты NAT не проверены'));
   if(!conflict&&links.has(p)){const link=links.get(p);card.append(add(el('div','vpn-port-link'),el('strong','','ID камеры B2O: '+link.camera_id),live('Копировать ID',()=>rpc('copy',String(link.camera_id)).then(()=>toast('ID скопирован')).catch(error))))}
   if(!conflict&&(groups.get(p)||groups.get(String(p)))?.length){
    const matched=groups.get(p)||groups.get(String(p)),details=el('details','vpn-port-cameras');details.open=S.expandedPorts.has(p);
    details.append(el('summary','','Найдено камер регистратора: '+matched.length));
    for(const link of matched)details.append(add(el('div','vpn-port-link'),el('span','',link.label+' · ID '+link.camera_id),live('Копировать ID',()=>rpc('copy',String(link.camera_id)).then(()=>toast('ID скопирован')).catch(error))));
    details.ontoggle=()=>{if(S.client?.server===c.server&&S.client?.login===c.login){if(details.open)S.expandedPorts.add(p);else S.expandedPorts.delete(p)}};card.append(details)
   }
   list.append(card)
  }
  if(!c.ports.length)list.append(notice('Порты не найдены.'));
  root.append(list,el('p','muted',S.refreshWarning||'Автообновление каждые 15 секунд; конфликты NAT проверяются при полном обновлении.'));
 }
 function startRefresh(){
  stopRefresh();const selected=S.client;if(!selected||preview||page!=='search')return;
  const x={host:selected.server,login:selected.login,owner:identity,stopped:false};refresh=x;
  const next=()=>{if(refresh===x&&!x.stopped)x.timer=setTimeout(poll,15000)};
  async function poll(){
   if(refresh!==x||x.stopped)return;
   if(page!=='search'||identity!==x.owner||S.client?.server!==x.host||S.client?.login!==x.login){stopRefresh();return}
   if(document.hidden||taskBusy||idleRead||S.plan||$ ('command-dialog')?.open){next();return}
   
   try{
    const reply=await backgroundRead('client_refresh',{host:x.host,login:x.login});if(!reply)return;if(!reply.ok)throw Error(reply.error);
    if(refresh!==x||x.stopped)return;
    if(!reply.data.found){S.refreshWarning='Учётка не найдена при обновлении. Проверьте поиск; изменения не выполнялись.';return}
    preserveScroll(()=>{applyClientRefresh(reply.data.client);paintClient()});
   }catch{if(refresh===x)S.refreshWarning='Автообновление не подтверждено; показаны последние известные данные.'}
   finally{if(refresh===x&&!x.stopped){if(S.refreshWarning)paintClient();next()}}
  }
  next();
 }
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
   if(document.hidden||taskBusy||idleRead){x.timer=setTimeout(poll,2500);return}
   
   try{
    const result=await backgroundRead('traffic_sample',{host:x.host,login:x.login});if(!result.ok)throw Error(result.error);
    if(x.stopped||traffic!==x)return;
    const s=result.data,p=x.last,dt=p?s.sample_time-p.sample_time:0;
    const valid=p&&s.online&&p.online&&s.interface===p.interface&&dt>=.2&&s.rx>=p.rx&&s.tx>=p.tx;
    x.points.push({rx:valid?(s.rx-p.rx)*8/dt/1e6:null,tx:valid?(s.tx-p.tx)*8/dt/1e6:null});if(x.points.length>60)x.points.shift();x.last=s;x.error=null;
   }catch(err){if(traffic===x){x.error='Не удалось получить новый образец трафика.';x.last=null;x.points.push({rx:null,tx:null});if(x.points.length>60)x.points.shift()}}
   finally{if(!x.stopped&&traffic===x){drawTraffic();x.timer=setTimeout(poll,2500)}}
  }
  x.timer=setTimeout(poll,10);
 }
 function renderClient(root){
  const c=S.client;if(!c){if(currentPlan())previewNotice(root,currentPlan(),out=>{if(out?.restored&&out.client){S.client=out.client;S.camPorts=null;S.clientHistory=null;searchState=null;S.newPorts=[];startRefresh()}});resultNote(root);return;}
  const workspace=el('div','vpn-client-workspace'),left=el('div','panel vpn-client-column'),right=el('div','panel vpn-client-column');
  const online=el('span',c.is_online?'online':'offline',c.is_online?'В сети':'Не в сети');online.id='client-online';
  left.append(add(el('div','vpn-client-heading'),el('h2','','Клиент '+c.login),online));
  right.append(el('h2','','Управление аккаунтом'));
  const info=el('div','vpn-client-scroll'),detail=el('div','vpn-client-scroll');info.dataset.scrollKey='vpn-info';detail.dataset.scrollKey='vpn-actions';
  info.tabIndex=0;detail.tabIndex=0;info.setAttribute('aria-label','Данные клиента и порты');detail.setAttribute('aria-label','Управление аккаунтом');
  left.append(info);right.append(detail);workspace.append(left,right);root.append(workspace);
  const panel=el('div','vpn-live-card');panel.id='client-live-card';updateClientCard(panel,c);info.append(panel);if(!refresh)startRefresh();
  root=detail;
  trafficPanel(root,c);
  const cameraLogin=field("Уточнить логин B2O (необязательно)","text",S.cameraLogin||"");cameraLogin.input.oninput=()=>{S.cameraLogin=value(cameraLogin);invalidateClientPlan()};form(root,cameraLogin);action(root,live("Сверить NAT с камерами",()=>run(async()=>{S.cameraLogin=value(cameraLogin);S.camPorts=null;S.cameraMatchError='';S.camPorts=await op("camera_ports",{host:c.server,login:c.login,override:S.cameraLogin})}),"primary"));if(S.cameraMatchError)root.append(notice(S.cameraMatchError));
  const cameraBox=el('div');cameraBox.id='client-camera-result';root.append(cameraBox);
  if(S.camPorts){const q=S.camPorts;keyValue(cameraBox,[["Камер проверено",q.checked_cameras],["Подтверждено",q.matched_camera_count],["Ошибок",q.stream_errors]]);const linked=[...q.links,...q.grouped.flatMap(group=>group[1])];if(linked.length)cameraBox.append(table(["NAT-порт","ID камеры","Камера","Страна"],linked.map(x=>[x.port,x.camera_id,x.label,x.country])));if(q.ambiguous.length)cameraBox.append(notice("Неоднозначные порты: "+q.ambiguous.join(", ")));if(q.unmatched.length)cameraBox.append(notice("Без подтверждения: "+q.unmatched.join(", ")));if(q.incomplete)cameraBox.append(notice("Ответ B2O неполный. Не подтверждает отсутствие камер."))}
  section(root,'Действия с клиентом');
  const controls=el('div','panel vpn-control-panel'),writable=!preview&&!!identity?.writes;
  const prepare=(act,extra={})=>{const current=S.client;if(current)stage('vpn_preview',{host:current.server,login:current.login,action:act,...extra})};
  controls.append(el('h3','','Основное'),add(el('div','actionbar'),live('Копировать данные',()=>rpc('copyVpn',c.server,c.login).then(()=>toast('Конфигурация скопирована')).catch(error)),live('Обновить статус',refreshClient),button('Разорвать сессию',()=>prepare('disconnect'),'quiet',!writable)));
  if(identity?.role==='sysadmin')action(controls,live('WinBox',()=>run(()=>op('winbox',{host:c.server}))));
  controls.append(el('h3','','Управление портами'));
  const count=field('Количество новых портов','number',S.portCount||1);count.input.min=1;count.input.max=14;count.input.oninput=()=>{S.portCount=num(count);invalidateClientPlan()};form(controls,count);action(controls,button('Добавить порты',()=>prepare('add',{count:num(count)}),'primary',!writable));
  const port=el('p','muted');port.id='vpn-selected-port';controls.append(port);
  if(identity?.role==='sysadmin'){
   const toggle=button('Выберите порт',()=>{const p=S.selectedPort;if(p)prepare(S.client.disabled_ports.includes(p)?'port_enable':'port_disable',{port:p})});toggle.id='vpn-toggle-port';
   const remove=button('Удалить порт',()=>{if(S.selectedPort)prepare('remove_port',{port:S.selectedPort})});remove.id='vpn-remove-port';
   const recreate=button('Пересоздать порт',()=>{if(S.selectedPort)prepare('recreate_port',{port:S.selectedPort})});recreate.id='vpn-recreate-port';
   controls.append(add(el('div','actionbar'),toggle,remove,recreate),el('h3','','Учётная запись'));
   const pw=field('Новый пароль','password');pw.input.autocomplete='new-password';pw.input.oninput=invalidateClientPlan;form(controls,pw);
   action(controls,button('Сменить пароль',()=>{const password=pw.input.value;pw.input.value='';prepare('password',{password})},'quiet',!writable));
   const account=button('Вкл./выкл. учётку',()=>prepare(S.client.is_enabled?'disable':'enable'));account.id='vpn-account-toggle';controls.append(add(el('div','actionbar'),account));
   const d=field('Для удаления: точный server/login','text');d.input.oninput=invalidateClientPlan;form(controls,d);action(controls,button('Удалить клиента',()=>stage('delete_preview',{host:c.server,login:c.login,confirm:value(d)}),'quiet',!writable||!identity?.destructive))
  }
  root.append(controls);paintPortControls();
  previewNotice(root,currentPlan(),out=>{if(out?.deleted){stopTraffic();stopRefresh();syncResult(c,true);S.client=null;S.newPorts=[]}else if(out?.login){S.client=out;S.newPorts=Array.isArray(out.new_ports)?out.new_ports:(S.newPorts||[]).filter(p=>out.ports.includes(p));if(S.newPorts.length)S.selectedPort=S.newPorts[0];S.camPorts=null;S.clientHistory=null;syncResult(out);startRefresh()}else if(out?.disconnected!=null){S.refreshWarning='Сессия разорвана; ожидается повторная проверка.';startRefresh()}});resultNote(root);clientHistory(root,c);
 }
 function inactive(root){
  root.append(hero('VPN / LIFECYCLE','VPN-клиенты','Жизненный цикл учёток на всех серверах. Частичный ответ не считается полным результатом.'));
  if(!preview&&identity?.role!=='sysadmin'){root.append(notice('Раздел доступен системному администратору.'));return}
  if(!ensureHosts(root))return;
  const h=serverField(),days=field('Порог неактивности, дней','number',S.inactiveDays||30),scope=field('Область проверки','select',S.inactiveScope||'all',[['all','Все доверенные серверы'],['one','Выбранный сервер']]),unknown=field('Неизвестное состояние','select',S.inactiveUnknown||'yes',[['yes','Показать'],['no','Скрыть']]);form(root,scope,h,days,unknown);
  action(root,live('Проверить серверы',()=>run(async()=>{S.inactiveDays=num(days);S.inactiveScope=value(scope);S.inactiveUnknown=value(unknown);S.inactiveSelected=[];S.inactive=null;S.inactiveBatch=null;S.plan=null;S.inactive=await op('inactive_fleet',{host:setHost(h),all:S.inactiveScope==='all',days:S.inactiveDays,unknown:S.inactiveUnknown==='yes'})}),'primary'));
  if(S.inactive){const result=S.inactive,labels={A:'Активная',S:'Сон',Q:'Карантин',R:'Архив',M:'Ручная',U:'Неизвестно'};keyValue(root,[['Найдено',result.rows.length],['Серверов',result.total],['Ошибок',result.errors.length]]);if(result.errors.length)root.append(notice('Недоступны: '+result.errors.join(', ')));if(result.cancelled)root.append(notice('Проверка отменена, вывод неполный.'));
   const filter=field('Состояние LV','select',S.lifeFilter||'', [['','Все'],...Object.entries(labels)]),order=field('Сортировка','select',S.lifeOrder||'old',[['old','Сначала старые'],['new','Сначала новые'],['server','По серверу'],['login','По логину']]),query=field('Фильтр логина','text',S.lifeQuery||'');form(root,filter,order,query);action(root,button('Применить фильтр',()=>{S.lifeFilter=value(filter);S.lifeOrder=value(order);S.lifeQuery=value(query);render(false)}));
   const rows=result.rows.filter(x=>(!S.lifeFilter||x.lifecycle_state===S.lifeFilter)&&(!S.lifeQuery||x.login.toLowerCase().includes(S.lifeQuery.toLowerCase()))).sort((a,b)=>S.lifeOrder==='new'?String(b.last_seen||'').localeCompare(a.last_seen||''):S.lifeOrder==='server'?a.server.localeCompare(b.server):S.lifeOrder==='login'?a.login.localeCompare(b.login):String(a.last_seen||'9999').localeCompare(b.last_seen||'9999'));const items=(S.inactiveSelected||[]),key=x=>x.server+'|'+x.login;
   const listRoot=el('div','panel inactive-list');listRoot.append(el('h2','','Учётные записи'));root.append(listRoot);listRoot.append(table(['','Логин','Сервер','Состояние LV','Последний выход','Учётка',''],rows.map(c=>{const ch=el('input','camera-check');ch.type='checkbox';ch.checked=items.some(x=>key(x)===key(c));ch.disabled=identity?.role!=='sysadmin'||c.is_online||result.cancelled;ch.setAttribute('aria-label','Выбрать '+c.login);ch.onchange=()=>{S.inactiveSelected=(S.inactiveSelected||[]).filter(x=>key(x)!==key(c));if(ch.checked)S.inactiveSelected.push(c);discardPlan();root.querySelector('.confirmation')?.remove();$('inactive-count').textContent='Выбрано: '+S.inactiveSelected.length};return [ch,c.login,c.server,labels[c.lifecycle_state]||c.lifecycle_state,c.last_logged_out||'—',c.is_enabled?'Включена':'Отключена',live('Карточка',()=>{window.lvModules.clearClient();go('search');client(c.server,c.login)})]})));
   const count=el('span','muted','Выбрано: '+items.length);count.id='inactive-count';root.append(add(el('div','actionbar'),count,button('Выбрать до 50',()=>{discardPlan();S.inactiveSelected=rows.filter(x=>!x.is_online).slice(0,50);render(false)},'quiet',identity?.role!=='sysadmin'||result.cancelled),button('Снять выбор',()=>{discardPlan();S.inactiveSelected=[];render(false)})));
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
 function clearBillingView(){stopThumbs();S.billingThumbs=null;S.billingEdit=null;S.billingCardTab='services';S.billingHistoryCamera=null;S.billingClient=null;S.hits=null;S.history=null;S.catalog=null;S.catalogFilter={};S.billingImage=null;S.bulkSelected=[];S.bulkResult=null;S.recipients=null;S.plan=null;S.outcome=null}
 function clearB2OView(){stopThumbs();S.billingThumbs=null;S.cameraThumbs=null;S.b2oDraft=null;S.b2oCreated=null;S.b2oAccess=null;S.b2oClient=null;S.b2oReadId=null;S.archive=null;S.archiveSaved=null;S.cameraInfo=null;S.cameraChoices=null;S.cameraLive=null;S.cameraImage=null;S.billingImage=null;S.agentImage=null;S.camPorts=null;S.plan=null;S.outcome=null}
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
 function billingCatalog(root,scope){
  if(scope==='all')action(root,live('Обновить из биллинга',()=>run(async()=>{S.catalog=null;S.catalogFilter={};S.catalog=await op('billing_catalog',{scope:'all'})}),'primary'));
  if(!S.catalog||S.catalog.scope!==scope){if(scope==='all')root.append(notice('Список тарифов пока не загружен.'));return}
  if(S.catalog&&S.catalog.scope===scope){
   section(root,scope==='all'?'Все тарифы':'Каталог тарифов клиента');const cat=S.catalog,f=S.catalogFilter||{},country=field('Страна','select',f.country||'',[['','Все'],...(cat.countries||[]).map(x=>[x.name,x.name])]),type=field('Тип','select',f.account_type||'',[['','Все'],...(cat.account_types||[]).map(x=>[x,x])]),q=field('Название / ID','text',f.query||''),days=field('Глубина архива, дней (0 — все)','number',f.days||0),rate=field('Битрейт, Мбит/с (0 — все)','number',f.bitrate||0);
   days.input.min=0;rate.input.min=0;rate.input.step='.1';country.input.disabled=cat.scope==='client';form(root,country,type,q,days,rate);
   action(root,live('Применить фильтры',()=>run(async()=>{S.catalogFilter={country:value(country),account_type:value(type),query:value(q),days:num(days),bitrate:num(rate)};S.catalog=await op('billing_filter',S.catalogFilter)}),'primary'));
   action(root,live('Сбросить фильтры',()=>run(async()=>{S.catalogFilter={};S.catalog=await op('billing_filter',{})})));
   root.append(notice('Показано '+cat.tariffs.length+' из '+cat.total+' тарифов. Срок архива извлекается из названия, а не из тестового периода.'));
   root.append(table(['ID','Название','Страна','Тип','Архив, дн.','Мбит/с','Цена','Пробный период','Описание'],cat.tariffs.map(t=>[t.id,t.name,t.country,t.account_type,t.archive_days,t.bitrate_mbps,t.price+' '+t.currency,t.test_days,t.description])));
  }
 }
 function billingTabs(root,items,active,onSelect,kind){
  const bar=el('div','desktop-section-nav billing-tabs');bar.setAttribute('role','group');bar.setAttribute('aria-label',kind==='workspace'?'Клиенты и тарифы':'Карточка клиента');
  for(const [id,label] of items){const b=button(label,()=>onSelect(id),'quiet',taskBusy);b.dataset.billingTab=kind+':'+id;b.setAttribute('aria-pressed',String(active===id));bar.append(b)}root.append(bar)
 }
 function billing(root){
  root.append(hero('CRM / BILLING','Клиенты и тарифы','Поиск, камеры и управление тарифами.'));
  const tab=S.billingTab||'search';
  billingTabs(root,[['search','Поиск клиента'],['create','Создать клиента'],['all','Все тарифы']],tab,id=>{if(id===tab)return;discardPlan();S.billingTab=id;S.catalog=null;S.catalogFilter={};S.billingEdit=null;render(true)},'workspace');
  if(tab==='create'){sessionStrip(root,'b2o');billingCreate(root);return}
  sessionStrip(root,'crm');
  if(tab==='all'){billingCatalog(root,'all');return}
  const q=field('Логин или номер учётной записи','text',S.billingQuery||''),search=el('form','panel billing-search-form');
  q.input.placeholder='Введите логин клиента, например lkz_...';q.input.maxLength=100;q.input.required=true;
  const find=el('button','primary','Найти клиента');find.type='submit';find.disabled=preview||!S.admin?.crm;
  search.append(q.node,find);root.append(search);search.onsubmit=e=>{e.preventDefault();if(find.disabled||taskBusy)return;const query=value(q);if(!query)return;run(async()=>{S.billingQuery=query;clearBillingView();S.hits=await op('billing_search',{query});if(S.hits.length===1){S.billingClient=await op('billing_select',{id:S.hits[0].id});S.billingCardTab='services'}})};
  if(S.hits?.length>1)root.append(table(['Логин','Клиент','Страна','Баланс',''],S.hits.map(x=>[x.login,x.account_name,x.country,x.balance_text,live('Открыть',()=>run(async()=>{S.billingClient=null;discardPlan();S.history=null;S.catalog=null;S.catalogFilter={};S.billingImage=null;S.bulkSelected=[];S.bulkResult=null;S.recipients=null;S.billingEdit=null;S.billingCardTab='services';S.billingClient=await op('billing_select',{id:x.id})}))])));
  const c=S.billingClient;
  if(!c){if(S.bulkResult){section(root,'Результаты массового изменения');root.append(table(['Камера','Результат'],S.bulkResult.map(x=>[x.camera,({confirmed:'Подтверждено',unknown:'Неизвестный результат',not_confirmed:'Не подтверждено',not_attempted:'Не отправлено'})[x.status]||x.status])));root.append(notice('Не удалось перечитать карточку клиента. Выполните поиск заново.'))}root.append(add(el('div','panel billing-empty'),el('h2','',S.hits?.length===0?'Клиент не найден':'Найдите клиента'),el('p','muted',S.admin?.crm?'Введите логин или номер учётной записи.':'Войдите в корпоративный биллинг, чтобы искать клиентов.')));return}
  const header=el('div','panel billing-client-summary');header.append(add(el('div'),el('h2','',c.display_name||c.login),el('p','muted',[c.legal_type,c.country,c.login].filter(Boolean).join(' · '))),el('span',['активный','активен','active'].includes(String(c.status).toLowerCase())?'online':'muted',c.status||'Неизвестно'),live('Обновить карточку',()=>run(async()=>{discardPlan();S.catalog=null;S.catalogFilter={};S.history=null;S.billingImage=null;S.bulkSelected=[];S.recipients=null;S.billingEdit=null;S.billingClient=await op('billing_refresh')})));root.append(header);
  const cardTab=S.billingCardTab||'services';billingTabs(root,[['info','Общая информация'],['services','Услуги']],cardTab,id=>{if(id===cardTab)return;discardPlan();S.billingCardTab=id;render(false)},'client');
  if(cardTab==='info'){const panel=el('div','panel');keyValue(panel,[['Логин',c.login],['Договор №',c.contract_number],['Email',c.email],['Телефон',c.phone],['Менеджер',c.manager],['Адрес',c.address],['Дата регистрации',c.registered_at],['Страна',c.country],['Баланс',c.balance_text]]);root.append(panel);return}
  billingServices(root,c);
 }
 function billingServices(root,c){
  const panel=el('div','panel billing-services');panel.append(el('h2','','Услуги видеонаблюдения'));
  const loadCatalog=async()=>{if(S.catalog?.scope!=='client'){S.catalog=null;S.catalogFilter={};S.catalog=await op('billing_catalog',{scope:'client'})}};
  const openEdit=(id,kind)=>run(async()=>{discardPlan();S.billingEdit={id,kind};S.recipients=null;if(kind==='tariff'||kind==='bulk')await loadCatalog()});
  const count=el('span','muted','Выбрано: '+(S.bulkSelected||[]).length);count.id='bulk-count';
  const bulk=live('Изменить выбранным ('+(S.bulkSelected||[]).length+')',()=>openEdit(null,'bulk'),'primary');
  bulk.disabled=preview||!identity?.writes||!(S.bulkSelected||[]).length;
  const updateCount=()=>{const n=(S.bulkSelected||[]).length;count.textContent='Выбрано: '+n;bulk.textContent='Изменить выбранным ('+n+')';bulk.disabled=preview||!identity?.writes||!n};
  panel.append(add(el('div','actionbar'),count,bulk,live('Каталог тарифов',()=>run(async()=>{discardPlan();S.billingEdit=null;await loadCatalog()})),live('Выбрать все',()=>{discardPlan();S.bulkSelected=c.services.filter(x=>x.id===x.camera_id).map(x=>x.id).slice(0,100);render(false)}),live('Снять выбор',()=>{discardPlan();S.bulkSelected=[];render(false)})));
  if(!c.services_loaded)panel.append(notice('CRM не подтвердила загрузку списка камер.'));
  const thumbs=thumbnailState('billing',c);const list=el('div','billing-camera-list');list.dataset.scrollKey='billing-cameras';list.tabIndex=0;
  if(!c.services.length)list.append(notice('Нет загруженных услуг.'));
  for(const x of c.services){
   const check=el('input','camera-check');check.type='checkbox';check.setAttribute('aria-label','Выбрать '+x.camera_name);check.checked=(S.bulkSelected||[]).includes(x.id);check.disabled=preview||x.id!==x.camera_id;
   check.onchange=()=>{discardPlan();const ids=(S.bulkSelected||[]).filter(id=>id!==x.id);if(check.checked&&ids.length>=100){check.checked=false;toast('Можно выбрать до 100 камер.')}S.bulkSelected=check.checked?[...ids,x.id]:ids;updateCount();document.querySelector('.billing-services .confirmation')?.remove()};
   const card=el('article','billing-camera-card');card.dataset.billingCamera=x.id;
   const thumb=el('div','billing-camera-thumb');thumb.dataset.thumbKey=x.id;const img=S.billingImage?.camera===x.id?S.billingImage.src:thumbs.images.get(x.id);
   thumb.append(el('span','muted','Нет превью'));if(img)showThumb(thumb,img);
   const main=el('div','billing-camera-details');main.append(add(el('div','billing-camera-title'),el('h3','',x.camera_name||'Камера '+x.camera_id),el('span',x.status==='Активный'?'online':'muted',x.status)),el('p','','Тариф: '+(x.tariff_name||'—')),el('p','muted','ID CRM: '+(x.camera_id||'—')+' · ID B2O: '+(x.camera_vc_id||'—')),el('p','muted','Цена: '+(x.price_text||'—')+' · Скидка: '+(x.discount_text||'—')+' · Итого: '+(x.total_text||'—')));
   const actions=el('div','actionbar');const writable=!!identity?.writes&&x.id===x.camera_id;
   actions.append(live('Кадр',()=>run(async()=>{S.billingImage=await op('billing_camera_image',{camera:x.id})})),button('Изменить тариф',()=>openEdit(x.id,'tariff'),'primary',preview||!writable),live('История',()=>run(async()=>{discardPlan();S.billingHistoryCamera=x.id;S.history=await op('billing_history',{camera:x.id})})),button('Статус',()=>openEdit(x.id,'status'),'quiet',preview||!writable),button('Перенос камеры',()=>openEdit(x.id,'transfer'),'quiet',preview||!writable));main.append(actions);card.append(check,thumb,main);list.append(card);
  }
  panel.append(list,el('p','muted','Выберите камеру и тариф. Изменения выполняются только после подтверждения.'));
  queueThumbs('billing',c,c.services.filter(x=>x.id===x.camera_id&&x.camera_vc_id).slice(0,12).map(x=>({key:x.id,method:'billing_camera_image',args:{camera:x.id}})),thumbs);
  const edit=S.billingEdit;
  if(edit){
   const selected=c.services.find(x=>x.id===edit.id);
   const formRoot=el('div','panel billing-edit');formRoot.append(add(el('div','billing-camera-title'),el('h3','',edit.kind==='bulk'?'Изменить выбранным':(selected?.camera_name||'Камера')+' · '+({tariff:'Тариф',status:'Статус',transfer:'Перенос'})[edit.kind]),button('Закрыть',()=>{discardPlan();S.billingEdit=null;render(false)})));
   if(edit.kind==='tariff'||edit.kind==='bulk'){
    const options=(S.catalog?.scope==='client'?S.catalog.tariffs:[]).filter(x=>x.country===c.country);
    const tariff=field('Тариф','select','',[['','Выберите тариф'],...options.map(x=>[x.id,x.name+' · '+x.price+' '+x.currency])]),date=field('Дата','date'),comment=field('Комментарий','text');form(formRoot,tariff,date,comment);
    const submit=live('Подготовить изменение',()=>{if(!value(tariff)){toast('Выберите тариф.');return}const args={tariff:value(tariff),date:value(date),comment:value(comment)};if(edit.kind==='bulk')stage('billing_bulk_preview',{...args,cameras:S.bulkSelected||[]});else stage('billing_preview',{...args,camera:edit.id,action:'tariff'})},'primary');submit.disabled=preview||!identity?.writes||!options.length;action(formRoot,submit);
    if(!options.length)action(formRoot,live('Загрузить каталог',()=>run(loadCatalog)));
   }else if(edit.kind==='status'){const expected=selected?.status==='Активный'?0:selected?.status==='Неактивный'?1:null;const st=field('Статус','select',expected??'',expected===null?[['','Не подтверждён']]:[[expected,expected?'Включить':'Отключить']]),date=field('Дата','date'),comment=field('Комментарий','text');form(formRoot,st,date,comment);const submit=live('Подготовить изменение',()=>stage('billing_preview',{camera:edit.id,action:'status',status:num(st),date:value(date),comment:value(comment)}),'primary');submit.disabled=preview||!identity?.writes||expected===null;action(formRoot,submit)}
   else if(edit.kind==='transfer'){const q=field('Логин получателя','text',S.recipientQuery||'');form(formRoot,q);action(formRoot,live('Найти получателя CRM',()=>run(async()=>{S.recipientQuery=value(q);S.recipients=await op('billing_recipient',{query:S.recipientQuery})})));if(S.recipients?.length){const to=field('Получатель','select','',S.recipients.map(r=>[r.id,r.login+' · '+r.country+' · '+r.account_name]));form(formRoot,to);action(formRoot,live('Подготовить перенос',()=>stage('billing_transfer_preview',{recipient:value(to),camera:edit.id}),'primary'))}}
   panel.append(formRoot);
  }
  previewNotice(panel,currentPlan(),out=>{if(out?.items){S.billingClient=out.client;S.bulkResult=out.items}else S.billingClient=out;S.billingEdit=null;S.bulkSelected=[];S.history=null;S.billingImage=null;S.recipients=null});
  if(S.bulkResult){section(panel,'Результаты массового изменения');const labels={confirmed:'Подтверждено',unknown:'Неизвестный результат',not_confirmed:'Не подтверждено',not_attempted:'Не отправлено'};panel.append(table(['Камера','Результат'],S.bulkResult.map(x=>[x.camera,labels[x.status]||x.status])))}
  if(S.history){section(panel,'История камеры · '+(S.billingHistoryCamera||''));panel.append(table(['Дата','Пользователь','Действие','Поле','До','После'],S.history.map(h=>[h.date,h.user,h.action,h.field_name,h.old_value,h.new_value])))}
  resultNote(panel);root.append(panel);
  if(!edit||edit.kind==='tariff'||edit.kind==='bulk')billingCatalog(root,'client');
 }
 function billingCreate(root){
  const layout=el('div','create-workspace'),left=el('div','panel create-form'),right=el('div','panel create-result');
  left.append(el('h2','','Добавить клиента в админке B2O'),el('p','muted','Обычный аккаунт с подключением биллинга. Используются только проверенные операции существующего B2O-сервиса.'));
  const draft=S.b2oDraft||{},country=field('Страна','select',draft.country||'Россия',[['Россия','Россия'],['Казахстан','Казахстан'],['Беларусь','Беларусь']]),name=field('Имя клиента','text',draft.name||''),login=field('Логин клиента','text',draft.login||''),password=field('Пароль клиента','password'),phone=field('Телефон','text',draft.phone||'');
  for(const f of [name,login,password])f.input.required=true;password.input.autocomplete='new-password';
  const capture=()=>{S.b2oDraft={country:value(country),name:value(name),login:value(login),phone:value(phone)}};
  for(const f of [country,name,login,password,phone])f.input.addEventListener('input',()=>{capture();discardPlan();left.querySelector('.confirmation')?.remove()});
  const generate=button('Создать пароль (9 символов)',()=>{const alphabet='ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789',limit=256-256%alphabet.length;let out='';while(out.length<9){for(const x of crypto.getRandomValues(new Uint8Array(16))){if(x<limit)out+=alphabet[x%alphabet.length];if(out.length===9)break}}password.input.value=out;password.input.dispatchEvent(new Event('input'))});
  const formNode=el('form','billing-create-fields');const access=live('Проверить B2O',()=>run(async()=>{capture();S.b2oAccess=null;S.b2oAccess=await op('b2o_access',{country:value(country)})}));formNode.append(country.node,access);if(S.b2oAccess?.country===value(country))formNode.append(notice('Доступ к оператору подтверждён: '+S.b2oAccess.country));formNode.append(name.node,login.node,password.node,generate,phone.node,el('p','muted','Тип аккаунта: Обычный · Передать в биллинг: Да'));
  const submit=el('button','primary','Создать клиента у оператора');submit.type='submit';submit.disabled=preview||!identity?.writes||!S.admin?.b2o;formNode.append(submit);left.append(formNode);
  formNode.onsubmit=e=>{e.preventDefault();if(submit.disabled||taskBusy)return;capture();const pw=password.input.value;password.input.value='';S.b2oCreated=null;stage('b2o_create_preview',{...S.b2oDraft,password:pw})};
  previewNotice(left,currentPlan(),out=>{S.b2oCreated=out});
  right.append(el('h2','','Данные клиента'));if(S.b2oCreated?.id_account){keyValue(right,[['ID аккаунта',S.b2oCreated.id_account],['Проверка чтением',S.b2oCreated.read_back_verified?'Да':'Не подтверждена']]);right.append(live('Скопировать данные клиенту',()=>rpc('copyB2OCreated',S.b2oCreated.id_account).then(()=>toast('Данные скопированы')).catch(error),'primary'),notice('Реквизиты доступны для копирования 5 минут после создания, не сохраняются в интерфейсе.'))}else right.append(add(el('div','create-empty'),el('span','create-empty-symbol','◇'),el('h3','','Клиент ещё не создан')));
  layout.append(left,right);root.append(layout);
  const read=el('div','panel');read.append(el('h3','','Проверить существующего клиента B2O'));const readCountry=field('Страна','select',S.b2oReadCountry||'Россия',[['Россия','Россия'],['Казахстан','Казахстан'],['Беларусь','Беларусь']]),account=field('ID аккаунта','number',S.b2oReadId||'');account.input.min=1;form(read,readCountry,account);action(read,live('Проверить клиента',()=>run(async()=>{S.b2oReadCountry=value(readCountry);S.b2oReadId=num(account);S.b2oClient=null;S.b2oClient=await op('b2o_read',{country:S.b2oReadCountry,id:S.b2oReadId})})));if(S.b2oClient)keyValue(read,[['ID',S.b2oClient.id_account],['Логин',S.b2oClient.login],['Имя',S.b2oClient.client_name],['Тип',S.b2oClient.account_type]]);root.append(read);
 }
 function archive(root,diagnose){
  root.append(hero(diagnose?'ARCHIVE / DIAGNOSIS':'ARCHIVE / DOWNLOAD',diagnose?'Диагностика архива':'Скачать архив','Период задаётся в локальном времени камеры. Источник — существующий DVR / B2O сервис.'));
  sessionStrip(root,'b2o');
  const mode=diagnose?'diagnostics':'archives',forms=S.archiveForms||(S.archiveForms={}),localDate=t=>{const x=new Date(t);return new Date(t-x.getTimezoneOffset()*60000).toISOString().slice(0,16)},now=Math.floor(Date.now()/60000)*60000;
  const saved=forms[mode]||(forms[mode]={camera:'',operator:241,start:localDate(now-300000),end:localDate(now),compare:[],server_compare:'',compare_limit:15});
  const cam=field('ID камеры','text',saved.camera),country=field('Страна','select',saved.operator,[[241,'Россия'],[1721,'Казахстан'],[1741,'Беларусь'],[1442,'Здравсервис / Goodline'],[621,'Провзор']]),start=field('Период с','datetime-local',saved.start),end=field('по','datetime-local',saved.end),server=field('Камеры vcore (необязательно, иначе автоподбор)','text',saved.server_compare),limit=field('Камер vcore для сравнения','number',saved.compare_limit);
  cam.input.placeholder='Например: 207728 или linkvideo_207728';cam.input.maxLength=128;limit.input.min=1;limit.input.max=15;
  const layout=el('div',diagnose?'archive-diagnosis-workspace':'create-workspace archive-workspace'),params=el('div','panel create-form');
  params.append(el('h2','create-step','1. '+(diagnose?'Камеры для проверки':'Параметры архива')),cam.node,country.node);
  const extra=el('div','archive-extra-rows');let rows=Array.isArray(saved.compare)?saved.compare:[];saved.compare=rows;
  function paintRows(){extra.replaceChildren();rows.forEach((v,i)=>{const f=field('Камера этого адреса '+(i+2),'text',v);f.input.maxLength=128;f.input.oninput=()=>{rows[i]=value(f)};extra.append(add(el('div','archive-extra-row'),f.node,button('×',()=>{rows.splice(i,1);paintRows()})))});if(rows.length<8)extra.append(button('+ Камера этого адреса',()=>{rows.push('');paintRows()}))}
  if(diagnose){paintRows();params.append(extra)}
  params.append(el('h2','create-step','2. Период'),add(el('div','archive-period'),start.node,end.node),el('p','muted','Время камеры. Переезды reserve-transfers сопоставляются через UTC+7 автоматически.'));
  for(const [key,f] of [['camera',cam],['operator',country],['start',start],['end',end],['server_compare',server],['compare_limit',limit]])f.input.addEventListener('input',()=>{saved[key]=f===country||f===limit?num(f):value(f)});
  if(diagnose)params.append(server.node,limit.node);
  const find=live(diagnose?'Проверить архив':'Найти архив',()=>run(async()=>{
    const a={camera:value(cam),operator:num(country),start:value(start),end:value(end),compare:rows.map(x=>x.trim()).filter(Boolean),server_compare:value(server).split(',').map(x=>x.trim()).filter(Boolean),compare_limit:num(limit)};
    if(!a.camera||!a.start||!a.end||a.end<=a.start){toast('Укажите камеру и корректный период.');return}
    S.archive=null;S.archiveSaved=null;S.archivePage=mode;S.reportTab='summary';S.archive=await op(diagnose?'archive_diagnose':'archive_discover',a)
  }),'primary');params.append(find);layout.append(params);
  if(!diagnose){
    const dest=el('div','panel create-result');dest.append(el('h2','create-step','3. Сохранение MP4'),el('p','muted','Имя файла формируется автоматически по камере и выбранному периоду.'));
    const folder=el('p','archive-folder',S.archiveFolder||'Папка «Видео» по умолчанию');folder.dataset.archiveFolder='true';dest.append(folder);
    dest.append(add(el('div','actionbar'),live('Папка',()=>run(async()=>{S.archiveFolder=await rpc('archiveFolder','choose')})),live('Скопировать путь',()=>run(async()=>{const p=await rpc('archiveFolder','get');await rpc('copy',p);toast('Путь скопирован')}))));
    layout.append(dest);
  }
  root.append(layout);
  const d=S.archive;if(d&&S.archivePage===mode){const results=el('div','panel archive-results');results.append(el('h2','',diagnose?'Отчёт диагностики':'Результат поиска'));root.append(results);root=results;const time=t=>new Date((t+d.timezone*3600)*1000).toISOString().slice(0,19).replace('T',' ');
   keyValue(root,[['Камера',d.camera],['Сервер',d.server],['Часовой пояс','UTC '+d.timezone],['Покрытие',Math.round(d.coverage*10)/10+'%'],['Пропуски',d.exact_timeline?d.gaps.length:'Не подтверждены'],['Запрошено, сек.',Math.round(d.requested_seconds)],['Найдено, сек.',Math.round(d.covered_seconds)],['Участков / HLS сегментов',d.exact_timeline?d.slices.length:d.hls_segments],['Проверено серверов',d.checked],['Ошибки',d.error_count]]);
   if(!d.exact_timeline)root.append(notice(d.hls_only?'Найден HLS-плейлист: точные DVR-разрывы неизвестны.':'Архив не подтверждён на проверенных источниках; это не доказательство удаления записи.'));
   if(d.hls_hosts?.length)keyValue(root,[['Серверы HLS',d.hls_hosts]]);if(d.archive_depth)keyValue(root,[['Глубина архива, дней',d.archive_depth]]);
   if(d.checked_hosts?.length)keyValue(root,[['Проверенные DVR',d.checked_hosts]]);
   if(d.exact_timeline&&d.requested_end>d.requested_start){const box=el('div','panel timeline-box'),svg=document.createElementNS('http://www.w3.org/2000/svg','svg');svg.setAttribute('viewBox','0 0 1000 38');svg.setAttribute('preserveAspectRatio','none');svg.setAttribute('role','img');svg.setAttribute('aria-label','Интервалы архива за выбранный период');const draw=(start,end,cls)=>{const n=document.createElementNS(svg.namespaceURI,'rect'),range=d.requested_end-d.requested_start,x=Math.max(0,(start-d.requested_start)/range*1000),w=Math.min(1000-x,(end-start)/range*1000);if(w<=0)return;n.setAttribute('x',x);n.setAttribute('y','6');n.setAttribute('width',Math.max(.7,w));n.setAttribute('height','26');n.setAttribute('class',cls);const title=document.createElementNS(svg.namespaceURI,'title');title.textContent=time(start)+' — '+time(end);n.append(title);svg.append(n)};draw(d.requested_start,d.requested_end,'timeline-base');for(const s of d.slices)draw(s.start,s.end,'timeline-video');for(const g of d.gaps)draw(g.start,g.end,'timeline-gap');box.append(el('h3','','Лента записи'),svg,add(el('div','timeline-axis'),el('span','',time(d.requested_start)),el('span','',time(d.requested_end))),add(el('div','timeline-legend'),el('span','','Запись'),el('span','','Разрыв')));root.append(box)}
   if(d.exact_timeline&&d.gaps.length){section(root,'Пропуски');root.append(table(['Начало','Конец'],d.gaps.map(g=>[time(g.start),time(g.end)])))}
   if(d.diagnosis){section(root,d.diagnosis.title);root.append(add(el('div','panel'),el('h3','',d.diagnosis.side+' · '+d.diagnosis.confidence),el('p','',d.diagnosis.summary),...d.diagnosis.cautions.map(c=>el('p','',c))));if(d.compared)keyValue(root,[['Камер адреса',d.compared.address],['Камер vcore',d.compared.server],['Ошибки сравнения',d.compared.failed]]);if(d.compared?.auto_error)root.append(notice('Автоматический подбор камер vcore недоступен. Вывод ограничен имеющимися данными.'));if(d.diagnosis.evidence?.length)root.append(table(['Факт','Детали'],d.diagnosis.evidence.map(e=>[e.title,e.detail])));if(d.diagnosis.gaps?.length)root.append(table(['Начало','Конец','Вывод'],d.diagnosis.gaps.map(g=>[time(g.start),time(g.end),g.title])))}
   if(d.moves?.length){section(root,'Переезды между серверами · UTC+7');const reserve=t=>t==null?'—':new Date((t+7*3600)*1000).toISOString().slice(0,19).replace('T',' ');root.append(table(['Откуда','Куда','Начало UTC+7','Конец UTC+7','Пересечение'],d.moves.map(e=>[e.from,e.to,reserve(e.start),reserve(e.end),e.overlap==null?'Не подтверждено':e.overlap?'Да':'Нет'])))}
   if(d.reports){section(root,'Отчёт диагностики');const tabs=el('div','actionbar');for(const [id,label] of [['summary','Итог'],['breaks','Разрывы'],['moves','Переезды'],['compare','Сравнение'],['technical','Технические детали']])tabs.append(button(label,()=>{S.reportTab=id;render(false)},S.reportTab===id?'primary':'quiet'));root.append(tabs);const report=d.reports[S.reportTab||'summary'];const reportText=el('pre','diagnosis-report',report);reportText.tabIndex=0;reportText.dataset.scrollKey='archive-report';root.append(reportText);action(root,live('Копировать отчёт',()=>rpc('copy',report).then(()=>toast('Отчёт скопирован')).catch(error)));action(root,live('Копировать все разделы',()=>rpc('copy',Object.values(d.reports).join('\n\n')).then(()=>toast('Отчёт скопирован')).catch(error)));
    for(const [title,rows] of [['Камеры адреса',d.address_compare],['Камеры vcore',d.server_compare]])if(rows?.length){section(root,title);root.append(table(['Камера','Сервер','Покрытие','Пропуски','Совпадение, сек.'],rows.map(x=>[x.camera||x.id,x.server,x.ok?x.coverage.toFixed(1)+'%':'Ошибка',x.exact_timeline?x.gaps.length:'—',x.overlap_seconds==null?'—':Math.round(x.overlap_seconds)])))}}

   if(!diagnose&&d.downloadable){
    const method=field('Метод скачивания','select',S.downloadMethod||'ffmpeg',[['ffmpeg','FFmpeg со звуком'],['ffmpeg_no_audio','FFmpeg без звука'],['curl','Прямой MP4 / curl']]);
    const manual=field('Ручная команда FFmpeg (необязательно)','textarea',S.manualCommand||'');
    manual.input.placeholder='ffmpeg -y -nostdin -hide_banner -loglevel error -progress pipe:1 -rw_timeout 30000000 -i "{input}" -c copy "{output}"';
    form(root,method,manual);
    const command=audio=>'ffmpeg -y -nostdin -hide_banner -loglevel error -progress pipe:1 -rw_timeout 30000000 -i "{input}" '+(audio?'-c copy':'-c:v copy -an')+' "{output}"';
    action(root,button('Шаблон со звуком',()=>{method.input.value='ffmpeg';manual.input.value=command(true)}));
    action(root,button('Шаблон без звука',()=>{method.input.value='ffmpeg_no_audio';manual.input.value=command(false)}));
    root.append(notice('Оставьте поле пустым для стандартной команды. В ручном режиме доступен ограниченный набор параметров; реальные URL и пути подставляет ядро.'));
    action(root,live(S.archiveSaved?'Скачать повторно':'Скачать найденный архив',()=>run(async()=>{S.downloadMethod=value(method);S.manualCommand=value(manual);S.archiveSaved=await rpc('archiveDownload',S.downloadMethod,S.manualCommand,d.download_token,d.filename);S.archiveFolder=await rpc('archiveFolder','get');toast((S.archiveSaved.partial?'Частично сохранён: ':'Сохранён: ')+S.archiveSaved.output)}),'primary'));
    if(S.archiveSaved){const s=S.archiveSaved;keyValue(root,[['Файл',s.output],['Сохранено, сек.',s.downloaded_seconds],['Пропущено, сек.',s.failed_seconds],['Ошибок',s.error_count]]);action(root,live('Открыть папку',()=>run(()=>rpc('archiveLocation'))));action(root,live('Копировать путь',()=>rpc('copy',s.output).then(()=>toast('Путь скопирован')).catch(error)))}
   }
  }
 }
 function agent(root){
  root.append(hero('CAMERA / AGENT','Новый Агент','Поиск камеры и настройки времени, ориентации, битрейта через исходный AgentService.'));
  const layout=el('div','create-workspace agent-workspace'),left=el('div','panel create-form'),right=el('div','panel create-result');layout.append(left,right);root.append(layout);root=left;
  left.append(el('h2','create-step','1. Найти камеру'));right.append(el('h2','','Статус и данные'));
  const sn=field('Серийный номер','text',S.serial||'');sn.input.placeholder='Серийный номер камеры LD';form(root,sn);
  const getSetting=async action=>{S.plan=null;S.agentSettings=S.agentSettings||{};if(action==='all')S.agentSettings={};else delete S.agentSettings[action];const out=await op('agent_settings',{action});Object.assign(S.agentSettings,out)};
  action(root,live('Найти камеру',()=>run(async()=>{S.serial=value(sn);S.agent=null;S.agentImage=null;S.agentSettings=null;S.agentOutcome=null;S.plan=null;S.agent=await op('agent_search',{serial:S.serial});if(S.admin?.b2o){try{S.agentImage=await op('agent_image')}catch{S.agentImage=null}}if(S.agentAction==='rotation'||S.agentAction==='bitrate')await getSetting(S.agentAction)}),'primary'));
  const c=S.agent;if(!c)right.append(add(el('div','create-empty'),el('span','create-empty-symbol','◇'),el('h3','','Камера ещё не выбрана'),el('p','muted','Введите серийный номер и нажмите «Найти камеру»')));if(c){keyValue(right,[['SN',c.serial_number],['Модель',c.model],['Производитель',c.vendor],['Сервер',c.server],['Версия агента',c.version],['Подключение',c.connect_time]]);
   action(right,live('Копировать данные',()=>rpc('copy',['Серийный номер: '+c.serial_number,'Agent-vcore: '+c.server,'Устройство: '+c.vendor+' · '+c.model,'Версия агента: '+c.version,'Подключение: '+(c.connect_time||'—')].join('\n')).then(()=>toast('Данные скопированы')).catch(error)));
   action(right,live('Обновить кадр',()=>run(async()=>{S.agentImage=null;S.agentImage=await op('agent_image')})));if(S.agentImage)imagePreview(right,S.agentImage);else right.append(el('p','muted','Превью камеры ещё не загружено.'));root.append(el('h2','create-step','2. Настройки камеры'));
   const kind=S.agentAction||'timezone';const tiles=el('div','desktop-section-nav agent-actions');for(const [id,label] of [['timezone','Время / NTP'],['rotation','Ориентация изображения'],['bitrate','Битрейт']]){const b=button(label,()=>{discardPlan();S.agentAction=id;S.agentOutcome=null;if(id==='timezone'||preview)render(false);else run(()=>getSetting(id))},'quiet',taskBusy);b.dataset.agentAction=id;b.setAttribute('aria-pressed',String(kind===id));tiles.append(b)}root.append(tiles);
   const s=S.agentSettings||{},rotNames=['Обычная','Вертикальное отражение','Горизонтальное отражение','180°'];
   if(kind!=='timezone')action(root,live('Обновить текущую настройку',()=>run(()=>getSetting(kind))));
   for(const e of s.errors||[])root.append(notice(e));
   const ready=kind==='timezone'||s[kind]!=null;
   const v=kind==='timezone'?field('Часовой пояс','select',S.agentTimezone??7,Array.from({length:27},(_,i)=>[i-12,'UTC'+(i>=12?'+':'')+(i-12)])):kind==='rotation'?field('Ориентация','select',s.rotation??0,rotNames.map((v,i)=>[i,v])):field('Битрейт, Кбит/с','number',s.bitrate??2048);
   if(kind!=='timezone')keyValue(root,[['Текущее значение',kind==='rotation'?rotNames[s.rotation]:s.bitrate]]);
   v.input.disabled=!ready;form(root,v);if(kind==='timezone')root.append(el('p','muted','Синхронизация с ntp.goodline.info · каждые 3600 секунд'));if(kind==='rotation'){const bar=el('div','desktop-section-nav');rotNames.forEach((name,i)=>bar.append(button(name,()=>{v.input.value=String(i)},'quiet',!ready)));root.append(bar)}
   if(kind==='bitrate'){v.input.min=1;v.input.max=100000;const bar=el('div','actionbar');for(const n of [512,1024,2048,4096,8192])bar.append(button(n===512?'512 Кбит/с':(n/1024)+' Мбит/с',()=>{v.input.value=String(n)},'quiet',!ready));root.append(bar)}
   const apply=live('Подготовить изменение',()=>{if(kind==='timezone')S.agentTimezone=num(v);S.agentOutcome=null;stage('agent_preview',{action:kind,value:num(v)})},'primary');apply.disabled=preview||!ready||!identity?.writes;action(root,apply);
   if(S.agentOutcome){const out=S.agentOutcome;if(out.serial===c.serial_number){if(out.stages){right.append(table(['Этап','Ответ'],out.stages.map(x=>[x.stage,x.accepted==null?'Не отправлено':x.accepted?'Принято HTTP '+x.http:'Не подтверждено'])));right.append(notice(out.accepted?'Все три запроса приняты; итоговое состояние времени отдельно не считано.':'Настройка могла примениться частично. Проверьте камеру перед повтором.'))}else if(out.confirmed)keyValue(right,[['Подтверждено чтением','Да'],['До',out.before],['После',out.after]])}}
  }
  previewNotice(root,currentPlan(),out=>{S.agentOutcome=out;if(out.confirmed&&S.agentSettings)S.agentSettings[out.action]=Number(out.after)});resultNote(root);
 }
 async function loadTemplates(){
  S.templates=await op('templates_list',{query:S.templateQuery||'',category:S.templateCategory||'',favorites:!!S.templateFavorites});
 }
 function enterPage(name){
  if(name!=='billing'&&name!=='camera')stopThumbs();
  if(name==='templates'&&!preview&&!S.templates&&!taskBusy)run(loadTemplates);
  if(name==='archives'&&!preview&&!S.archiveFolder&&!taskBusy)run(async()=>{S.archiveFolder=await rpc('archiveFolder','get')});
 }
 function templates(root){
  root.append(hero('SUPPORT / KNOWLEDGE','Шаблоны ответов','Личная библиотека быстрых ответов · локальное сохранение.'));
  const q=field('Поиск','text',S.templateQuery||''),cat=field('Категория','select',S.templateCategory||'',[['','Все'],...(S.templates?.categories||[]).map(v=>[v,v])]),fav=el('input');fav.type='checkbox';fav.checked=!!S.templateFavorites;q.input.placeholder='Найти шаблон или фразу…';
  const workspace=el('div','template-workspace'),library=el('div','panel template-library'),detail=el('div','panel template-detail');workspace.append(library,detail);root.append(workspace);
  const items=S.templates?.items||[],selected=items.find(t=>t.id===S.templateSelected)||S.templateCurrent;
  library.append(add(el('div','template-heading'),el('h2','','Библиотека'),el('span','muted',items.length+' ответов')),q.node,cat.node,add(el('label','checkline'),fav,el('span','','Только избранное')));
  const filter=()=>{S.templateQuery=value(q);S.templateCategory=value(cat);S.templateFavorites=fav.checked};
  const load=()=>{filter();return loadTemplates()};
  q.input.onkeydown=e=>{if(e.key==='Enter'&&!preview&&!taskBusy)run(load)};
  cat.input.onchange=fav.onchange=()=>{if(!preview&&!taskBusy)run(load)};
  library.append(add(el('div','actionbar'),live('Обновить',()=>run(load)),button('＋ Создать шаблон',()=>{S.editTemplate={title:'',category:S.templateCategory||'',body:''};S.templateSelected=null;S.templateCurrent=null;S.imageTemplate=null;S.images=null;render(false)},'primary')));
  const list=el('div','template-library-list');list.dataset.scrollKey='template-library';list.tabIndex=0;
  if(!items.length)list.append(notice('Пока нет ответов. Создайте шаблон или импортируйте DOCX.'));
  const select=async t=>{S.templateSelected=t.id;S.templateCurrent=t;S.editTemplate=null;S.imageTemplate=t.id;S.images=null;if(!preview)await loadImages(t.id)};
  const loadImages=async id=>{S.images=await op('templates_images',{id})};
  for(const t of items){const row=button('',()=>{},'template-list-item');row.onclick=()=>run(()=>select(t));row.dataset.templateId=String(t.id);row.setAttribute('aria-pressed',String(t.id===S.templateSelected));row.append(el('strong','',t.title+(t.favorite?' · ★':'')),el('small','muted',t.category),el('span','template-excerpt',t.body.slice(0,180)));list.append(row)}
  library.append(list,el('p','muted','Сохранение локальное · без отправки на сервер'));
  library.append(add(el('div','actionbar'),live('Импортировать DOCX',()=>run(async()=>{const r=await rpc('fileAction','templates_import',{category:value(cat)});await load();toast('Импортировано: '+r.count)})),live('Перенести библиотеку 3.x',()=>run(async()=>{S.migrateResult=await rpc('fileAction','templates_migrate');await load()}))));
  if(S.migrateResult)keyValue(library,[['Перенесено',S.migrateResult.imported],['Уже имеются',S.migrateResult.skipped],['Изображений',S.migrateResult.images],['Недоступные вложения',S.migrateResult.missing]]);
  detail.append(el('h2','','Ответ клиенту'));
  const editing=S.editTemplate;
  if(editing){
   const t=editing,title=field('Название шаблона','text',t.title||''),category=field('Категория','text',t.category||''),body=field('Текст ответа','textarea',t.body||'');
   for(const [k,f] of [['title',title],['category',category],['body',body]])f.input.oninput=()=>{t[k]=f.input.value};
   detail.append(title.node,category.node,body.node);
   detail.append(add(el('div','actionbar'),live('Сохранить изменения',()=>run(async()=>{const saved=await op('templates_save',{id:t.id||null,title:value(title),category:value(category),body:value(body)});S.editTemplate=null;await loadTemplates();const next=S.templates.items.find(x=>x.id===(saved?.id||t.id))||S.templates.items.find(x=>x.title===value(title));if(next)await select(next)}),'primary'),button('Отмена',()=>{S.editTemplate=null;render(false)})));
  }else if(selected){
   const t=selected;detail.append(el('h3','',t.title),el('p','muted',t.category),el('pre','template-answer',t.body));
   detail.append(add(el('div','actionbar'),live('Копировать ответ',()=>rpc('templateCopy',t.id).then(x=>toast(x.count?'Текст и '+x.count+' изображений в буфере'+(x.skipped?' · пропущено: '+x.skipped:''):'Текст скопирован')).catch(error),'primary'),button('Редактировать',()=>{S.editTemplate={...t};render(false)}),live(t.favorite?'★ В избранном':'☆ В избранное',()=>run(async()=>{await op('templates_favorite',{id:t.id,selected:!t.favorite});S.templateCurrent={...t,favorite:!t.favorite};await loadTemplates()})),live('Удалить шаблон',()=>{if(confirm('Удалить локальный шаблон?'))run(async()=>{await op('templates_delete',{id:t.id});S.templateSelected=null;S.templateCurrent=null;S.imageTemplate=null;S.images=null;await loadTemplates()})})));
  }else detail.append(add(el('div','create-empty'),el('span','create-empty-symbol','◇'),el('h3','','Выберите ответ из библиотеки')));
  const id=editing?.id||selected?.id;
  if(id){
   section(detail,'Вложения · '+(S.imageTemplate===id?(S.images||[]).length:0));
   detail.append(add(el('div','actionbar'),live('Добавить фото',()=>run(async()=>{await rpc('fileAction','templates_attach',{id});S.imageTemplate=id;await loadImages(id)})),live('Вставить из буфера',()=>run(async()=>{await rpc('templatePaste',id);S.imageTemplate=id;await loadImages(id)})),live('Обновить вложения',()=>run(async()=>{S.imageTemplate=id;await loadImages(id)}))));
   const attachments=el('div','template-attachments');
   if(S.imageTemplate===id)for(const img of S.images||[]){const im=el('img','template-image');if(/^data:image\/(png|jpeg|webp|gif);base64,/.test(img.src))im.src=img.src;im.alt='Вложение';attachments.append(add(el('div','panel'),im,live('Копировать фото',()=>rpc('templateCopy',id,img.id).then(()=>toast('Фото скопировано')).catch(error)),live('Убрать выбранное',()=>{if(confirm('Убрать изображение из шаблона?'))run(async()=>{await op('templates_detach',{id,image:img.id});await loadImages(id)})})))}
   detail.append(attachments);
  }else if(editing)detail.append(notice('Сначала сохраните новый шаблон, затем добавьте изображения.'));
 }
 function recovery(root){
  root.append(hero('VPN / RECOVERY','Резерв и восстановление','FULL backup — локальная копия конфигурации. PostgreSQL — основной источник восстановления; Google Sheets — аварийный.'));
  if(!preview&&identity?.role!=='sysadmin'){root.append(notice('Доступно системному администратору.'));return}
  if(!ensureHosts(root))return;const h=serverField();form(root,h);
  action(root,live('FULL backup сервера',()=>run(async()=>{S.backup=await op('backup',{host:setHost(h)})}),'primary'));
  action(root,live('FULL backup всех',()=>run(async()=>{S.backup=await op('backup',{all:true})})));
  if(S.backup){keyValue(root,[['Успешно',S.backup.success],['Ошибки',S.backup.failed],['Папка',S.backup.folder]]);root.append(table(['Сервер','Результат'],S.backup.servers.map(r=>[r.server,r.ok?'Подтверждён':'Ошибка'])))}
  section(root,'Локальные FULL backup');root.append(notice('Копии содержат пароли PPP. Не отправляйте их клиентам и не сохраняйте в общедоступные папки.'));
  action(root,live('Открыть папку копий',()=>run(()=>rpc('backupFolder'))));
  action(root,live('Загрузить список копий',()=>run(async()=>{S.backupInventory=await op('backup_inventory')})));
  if(S.backupInventory)root.append(table(['Копия','Создано UTC','Серверов OK','Ошибки','Файлов',''],S.backupInventory.map(x=>[x.id,x.created,x.success,x.failed,x.files,live('Проверить SHA-256',()=>run(async()=>{S.backupVerify=null;S.backupVerify=await op('backup_verify',{id:x.id})}))])));
  if(S.backupVerify){const v=S.backupVerify;root.append(notice(v.verified?(v.complete?'Контрольные суммы совпадают. Manifest отмечает все серверы успешными.':'Контрольные суммы совпадают, но резервная копия неполная.'):'Целостность не подтверждена или проверка отменена.'));root.append(table(['Файл','Проверка'],v.files.map(x=>[x.name,x.ok?'SHA-256 совпадает':'Не подтверждено'])))}
  section(root,'PostgreSQL · Архив удалённых VPN');
  const cq=field('Удалённый VPN (логин или порт)','text',S.cloudDeletedQuery||'');form(root,cq);
  action(root,live('Найти в PostgreSQL',()=>run(async()=>{S.cloudDeletedQuery=value(cq);S.cloudDeleted=null;S.cloudDeleted=await op('cloud_deleted_search',{query:S.cloudDeletedQuery})}),'primary'));
  if(S.cloudDeleted){if(S.cloudDeleted.error)root.append(notice(S.cloudDeleted.error));else root.append(table(['Логин','Сервер','VPN IP','Удалён','Порты','Пароль',''],S.cloudDeleted.rows.map(x=>[x.login,x.server,x.remote_address,x.deleted_at,x.ports.join(', '),x.password_saved?'Сохранён':'Нет',button('Восстановить',()=>stage('cloud_restore_preview',{host:x.server,login:x.login}),'quiet',preview||!x.password_saved)])))}
  section(root,'Google Sheets · Аварийное восстановление');action(root,live('Выбрать JSON-ключ',()=>run(async()=>{S.sheets=await rpc('fileAction','sheets_load')})));
  if(S.sheets?.configured)root.append(notice('Ключ принят в память текущего процесса.'));
  const q=field('Удалённый логин','text',S.deletedQuery||'');form(root,q);
  action(root,live('Искать удалённые',()=>run(async()=>{S.deletedQuery=value(q);S.deleted=null;S.deleted=await op('deleted_search',{query:S.deletedQuery})}),'primary'));
  action(root,live('Подготовить экспорт сервера в Sheets',()=>stage('sheets_preview',{host:setHost(h)})));action(root,live('Подготовить аварийный экспорт всех',()=>stage('sheets_preview',{all:true})));
  if(S.deleted)root.append(table(['Логин','Сервер','VPN IP','Удалён','Порты','Пароль',''],S.deleted.map(r=>[r.login,r.server,r.remote_address,r.deleted_at,r.ports,r.password_saved?'Сохранён':'Нет',button('Восстановить',()=>stage('restore_preview',{host:r.server,login:r.login}),'quiet',preview||!r.password_saved)])));
  previewNotice(root,currentPlan(),out=>{if(out?.sheets_results)S.sheetsResult=out;if(out?.restored){S.restoreResult=out;S.deleted=null;S.cloudDeleted=null}});if(S.restoreResult){const x=S.restoreResult;section(root,'Восстановленная учётка');keyValue(root,[['Логин',x.login],['Сервер',x.server],['VPN IP',x.client?.remote_address],['Порты',x.client?.ports],['NAT создано',x.nat_created],['Объектов VPNSync',x.restored_objects]]);if(x.archive_cleanup_confirmed===false)root.append(notice('VPN восстановлен, но удаление старой аварийной записи Sheets не подтверждено. Не запускайте восстановление повторно.'));if(x.port_replacements&&Object.keys(x.port_replacements).length)root.append(table(['Старый внешний порт','Новый внешний порт'],Object.entries(x.port_replacements)));action(root,live('Открыть карточку',()=>{go('search');client(x.server,x.login)}));action(root,live('Копировать VPN',()=>rpc('copyVpn',x.server,x.login).then(()=>toast('Скопировано')).catch(error)))}if(S.sheetsResult){section(root,'Результаты аварийного экспорта');root.append(table(['Сервер','Подтверждение','Клиентов','Изменено'],S.sheetsResult.sheets_results.map(x=>[x.server,x.success?'Подтверждено':'Не подтверждено',x.clients,x.changed])));if(S.sheetsResult.cancelled)root.append(notice('Экспорт остановлен; уже подтверждённые записи не отменяются.'))}resultNote(root);
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
  root.append(hero('B2O / CAMERAS','Информация по камере','Поиск по логину клиента, ID или серийному номеру, проверка потока и данных B2O.'));
  sessionStrip(root,'b2o');
  const inspect=async(camera,operator,choice=null)=>{
   S.cameraId=camera;S.cameraOperator=operator;S.cameraChoice=choice;S.cameraLive=null;S.cameraImage=null;S.cameraInfo=null;
   S.cameraInfo=await op('camera_inspect',{camera,operator});
  };
  if(!S.cameraInfo){
   const query=field('Логин клиента, ID или серийный номер','text',S.cameraLookupQuery||'');query.input.placeholder='Логин клиента, ID или серийный номер';query.input.required=true;query.input.maxLength=100;
   const box=el('form','panel billing-search-form'),find=el('button','primary','Найти камеры');find.type='submit';find.disabled=preview||!S.admin?.b2o;
   box.append(query.node,find);root.append(box);box.onsubmit=e=>{e.preventDefault();if(find.disabled||taskBusy||!value(query))return;run(async()=>{S.cameraLookupQuery=value(query);S.cameraChoices=null;S.cameraChoice=null;S.cameraInfo=null;S.cameraLive=null;S.cameraImage=null;S.cameraChoices=await op('camera_lookup',{query:S.cameraLookupQuery})})};
   const results=el('div','panel');results.append(el('h2','','Камеры'));root.append(results);
   if(S.cameraChoices){results.append(notice('Подтверждённых совпадений: '+S.cameraChoices.length));const list=el('div','billing-camera-list');list.dataset.scrollKey='camera-results';const thumbs=thumbnailState('camera',S.cameraChoices);
    for(const choice of S.cameraChoices){const card=el('article','camera-search-card');card.dataset.cameraChoice=choice.camera_id;const key=choice.operator_id+':'+choice.camera_id,thumb=el('div','billing-camera-thumb camera-result-thumb');thumb.dataset.thumbKey=key;thumb.append(el('span','muted','Нет превью'));showThumb(thumb,thumbs.images.get(key));const main=el('div','billing-camera-details');main.append(el('h3','',choice.label||choice.camera_id),el('p','muted','ID: '+choice.camera_id+' · '+choice.country),el('p','muted',[choice.customer_login?'Клиент: '+choice.customer_login:'',choice.tariff?'Тариф: '+choice.tariff:''].filter(Boolean).join(' · ')));card.append(thumb,main,el('span','muted',choice.status||'—'),live('Подробнее',()=>run(()=>inspect(choice.camera_id,choice.operator_id,choice)),'primary'));list.append(card)}results.append(list);queueThumbs('camera',S.cameraChoices,S.cameraChoices.slice(0,12).map(x=>({key:x.operator_id+':'+x.camera_id,method:'camera_lookup_image',args:{camera:x.camera_id,operator:x.operator_id}})),thumbs);
   }else results.append(notice('Введите логин клиента, ID или серийный номер камеры.'));
   const direct=el('details','panel camera-direct');direct.append(el('summary','','Прямая диагностика по ID и стране'));
   const id=field('ID камеры','text',S.cameraId||''),opid=field('Страна','select',S.cameraOperator||241,[[241,'Россия'],[1721,'Казахстан'],[1741,'Беларусь'],[1442,'Здравсервис / Goodline'],[621,'Провзор']]);form(direct,id,opid);action(direct,live('Диагностика камеры',()=>run(()=>inspect(value(id),num(opid))),'primary'));root.append(direct);return
  }
  root.append(add(el('div','panel search-compact'),el('strong','','Камера · '+(S.cameraInfo.name||S.cameraInfo.camera_id)),button('← К результатам',()=>{S.cameraInfo=null;S.cameraLive=null;S.cameraImage=null;render(true)})));
  const workspace=el('div','camera-detail-workspace'),facts=el('div','panel'),telemetry=el('div','panel');facts.append(el('h2','','Данные камеры'));telemetry.append(el('h2','','Поток и кадр'));workspace.append(facts,telemetry);root.append(workspace);root=facts;
  const c=S.cameraInfo;if(c)keyValue(root,[['Камера',c.name||c.camera_id],['Сервер',c.server],['IP',c.ip],['RTSP-порт',c.rtsp_port],['Ping',c.ping_text],['TCP',c.tcp_text],['Состояние',c.status],['Модель',c.model],['SN',c.serial],['Архив',c.archive_depth],['Последний поток',c.last_stream],['Оператор',c.operator],['ID аккаунта',c.account_id],['Адрес установки',c.address],['Логин клиента',S.cameraChoice?.customer_login||''],['Тариф',S.cameraChoice?.tariff||''],['Транскод B2O',c.transcode],['Проверка транскода',c.transcode_check],['Разрешение · B2O',c.b2o_resolution],['Видеокодек · B2O',c.b2o_video_codec],['Аудиокодек · B2O',c.b2o_audio_codec],['Битрейт · B2O (справочно)',c.b2o_bitrate]]);
  if(c){action(root,live('Копировать диагностику',()=>rpc('copy',['Камера: '+(c.name||c.camera_id),'Сервер: '+c.server,'Ping: '+c.ping_text,'TCP: '+c.tcp_text,'Состояние: '+c.status,...(S.cameraLive?['Видео: '+S.cameraLive.video_codec,'Разрешение: '+S.cameraLive.resolution,'FPS: '+S.cameraLive.fps,'Битрейт: '+S.cameraLive.bitrate_mbps]:[])].join('\n')).then(()=>toast('Диагностика скопирована')).catch(error)));action(telemetry,live('Последнее изображение B2O',()=>run(async()=>{S.cameraImage=null;S.cameraImage=await op('camera_image',{id:c.camera_id})})));imagePreview(telemetry,S.cameraImage);action(telemetry,live('Измерить поток',()=>run(async()=>{S.cameraLive=null;S.cameraLive=await op('camera_live',{id:c.camera_id})}),'primary'));if(S.cameraLive){const x=S.cameraLive;keyValue(telemetry,[['Битрейт, Мбит/с',x.bitrate_mbps],['Видео',x.video_codec],['Аудио',x.audio_codec],['Разрешение',x.resolution],['FPS',x.fps],['Источник',x.source],['Последний поток',x.last_stream]]);telemetry.append(notice(x.connectivity),notice(x.detail))}}

 }
 function dashboard(root){
  section(root,'Рабочие инструменты');const g=el('div','dashboard-grid');
  for(const [id,desc] of [['create','Создание учёток и NAT'],['inactive','Жизненный цикл VPN'],['lv','Скрипты и автоматизация'],['recovery','FULL backup · восстановление'],['billing','Тарифы и услуги'],['archives','Поиск и экспорт MP4'],['diagnostics','Разрывы и сравнение'],['camera','B2O и сетевые проверки'],['agent','Настройки камеры'],['templates','Библиотека ответов'],['admin','Независимые сессии']]){
   const p=pages.find(x=>x[0]===id);if(!p||!pageAllowed(id))continue;const b=btn('','dash-tile',()=>go(id));b.append(navIcon(id,p[1]),add(el('div'),el('strong','',p[2]),el('small','',desc)),el('span','dash-arrow','›'));g.append(b)
  }
  root.append(g);
 }
 function infrastructure(root,metric){
  if(identity?.role!=='sysadmin')return;
  const writable=!preview&&!!identity?.writes;
  if(!metric){
   const card=el('div','panel infrastructure-actions');card.append(el('h3','','Действия со всеми серверами'));
   const bar=el('div','actionbar');
   bar.append(live('Выгрузить все серверы',()=>run(async()=>{S.backup=await op('backup',{all:true})})),live('Обновить состояние LV',()=>run(async()=>{S.lvFleet=await op('lv_fleet')})));
   const hosts=(S.hosts||[]).map(h=>h.host),installed=(S.lvFleet?.rows||[]).filter(x=>x.installed&&!x.error).map(x=>x.server);
   for(const [act,label,targets] of [['install','Установить/обновить LV на всех',hosts],['start','Запустить LV на всех',installed],['pause','Остановить LV на всех',installed]])bar.append(button(label,()=>stage('lv_batch_preview',{action:act,hosts:targets}),'quiet',!writable||!targets.length||!!S.lvFleet?.cancelled));
   card.append(bar);
   const more=el('details','infrastructure-legend');more.append(el('summary','','Памятка состояний VPN-клиентов'),el('p','','A — активная · S — сон · Q — карантин · R — архив · M — ручная · U — неизвестно'));card.append(more);
   if(S.backup)keyValue(card,[['Копии успешно',S.backup.success],['Ошибки',S.backup.failed],['Папка',S.backup.folder]]);
   root.append(card);return;
  }
  const host=metric.server,card=el('div','panel infrastructure-actions'),state=S.lv?.server===host?S.lv:(S.lvFleet?.rows||[]).find(x=>x.server===host&&!x.error);
  card.append(el('h3','','Управление сервером · '+host));
  const bar=el('div','actionbar');bar.append(live('Выгрузить этот сервер',()=>run(async()=>{S.backup=await op('backup',{host})})),live('Проверить LV',()=>run(async()=>{discardPlan();S.lv=await op('lv_status',{host})})));
  for(const [act,label] of [['install',state?.installed?'Обновить LV':'Установить LV'],['seed','Инициализировать активность'],[state?.paused?'start':'pause',state?.paused?'Запустить LV':'Остановить LV'],[state?.aging_enabled?'quarantine_off':'quarantine_on',state?.aging_enabled?'Выключить карантин':'Включить карантин']])bar.append(button(label,()=>stage('lv_preview',{host,action:act}),'quiet',!writable||(act!=='install'&&!state?.installed)));
  card.append(bar);if(state)keyValue(card,[['LV',state.state_text],['Версия',state.version],['Карантин',state.aging_enabled?'Включён':'Выключен']]);else card.append(notice('Сначала получите подтверждённое состояние LV.'));
  root.append(card);
 }
 function infrastructureResults(root){
  if(identity?.role!=='sysadmin')return;
  previewNotice(root,currentPlan(),out=>{if(out.lv_results){S.lvBatch=out.lv_results;S.lvFleet=null}else S.lv=out});
  if(S.lvBatch)root.append(table(['Сервер','Результат','Статус LV'],S.lvBatch.map(x=>[x.server,({confirmed:'Подтверждено',not_confirmed:'Не подтверждено',not_attempted:'Не отправлено'})[x.status],x.state])));resultNote(root);
 }
 function workspaceSettings(root,tab){
  if(preview){root.append(notice('Настройки корпоративных серверов доступны после входа.'));return}
  if(tab==='cloud'){section(root,'Защищённые учётные данные');action(root,live('Очистить сохранённый вход',()=>run(()=>rpc('clearSaved','work'))));cloudSettings(root);return}
  if(tab==='archive'){
  section(root,'Архив · кластеры и серверы');
  action(root,live('Настройки архива',()=>run(async()=>{S.archiveSettings=await op('archive_settings')}),'primary'));
  if(S.archiveSettings){
   for(const item of S.archiveSettings.clusters){const b=el('div','panel setting-row');const sw=btn('','switch',()=>run(async()=>{S.archiveSettings=await op('archive_settings_update',{action:'cluster',cluster:item.key,enabled:!item.enabled})}));sw.setAttribute('role','switch');sw.setAttribute('aria-label',item.name);sw.setAttribute('aria-checked',String(item.enabled));sw.append(el('span'));b.append(add(el('div'),el('h3','',item.name),el('p','','Operator '+item.operator)),sw);root.append(b)}
   if(identity?.role==='sysadmin'){for(const group of S.archiveSettings.hosts){if(!group.custom.length)continue;section(root,group.country+' · '+group.operator);root.append(table(['Сервер',''],group.custom.map(host=>[host,live('Удалить',()=>run(async()=>{S.archiveSettings=await op('archive_settings_update',{action:'remove_host',operator:group.operator,host})}))])))}const operator=field('Оператор','select','241',S.archiveSettings.hosts.map(x=>[x.operator,x.country+' · '+x.operator])),host=field('Дополнительный архивный сервер','text');form(root,operator,host);action(root,live('Добавить сервер архива',()=>run(async()=>{S.archiveSettings=await op('archive_settings_update',{action:'add_host',operator:num(operator),host:value(host)})})))}
  }
  return}
  if(tab==='about'){
  section(root,'Журнал работы Helper 4');const logCard=el('div','panel');logCard.append(el('p','muted','Локальный журнал операций: время, название, результат и длительность. Без параметров, паролей, токенов и содержимого буфера обмена.'));
  const loadLogs=async()=>{S.logs=await rpc('logRead')};
  logCard.append(add(el('div','actionbar'),live('Обновить',()=>run(loadLogs),'primary'),live('Открыть папку',()=>run(()=>rpc('logFolder'))),live('Очистить',()=>run(async()=>{await rpc('logClear');await loadLogs()})),live('Скопировать',()=>rpc('copy',(S.logs||[]).map(x=>x.time+' '+x.action+' '+(x.ok?'OK':'ERROR')+' '+x.ms+' ms').join('\n')).then(()=>toast('Журнал скопирован')).catch(error))));
  if(S.logs){const out=el('pre','runtime-log',(S.logs||[]).map(x=>x.time+'  '+(x.ok?'OK   ':'ERROR')+'  '+x.action+'  '+x.ms+' ms').join('\n')||'Журнал пуст.');out.tabIndex=0;out.dataset.scrollKey='runtime-log';logCard.append(out)}root.append(logCard);return}
  if(tab!=='servers'||identity?.role!=='sysadmin'){root.append(notice('Доступно системному администратору.'));return;}
  section(root,'Реестр VPN-серверов');
  const load=async()=>{S.registry=await op('registry_list');S.hosts=await rpc('servers');serverState=null;searchState=null};
  action(root,live('Открыть реестр',()=>run(load),'primary'));
  if(S.registry){root.append(table(['Адрес','Регион','Доступ',''],S.registry.map(r=>[r.host,r.country,r.enabled?(r.trusted?'Активен':'Требуется доверие'):'Выключен',add(el('div','actionbar'),...(r.enabled&&r.trusted?[live('Проверить',()=>run(async()=>{S.registryTest=await rpc('analyze',r.host)}))]:[]),live(r.enabled?'Выключить':'Включить',()=>run(async()=>{await op('registry_update',{host:r.host,action:r.enabled?'disable':'enable'});await load()})),...(!r.builtin?[live('Доверять',()=>run(async()=>{await op('registry_update',{host:r.host,action:'trust',country:r.country});await load()})),live('Удалить',()=>run(async()=>{await op('registry_update',{host:r.host,action:'remove'});await load()}))]:[]))])));
   const h=field('Дополнительный DNS/IPv4','text'),c=field('Регион','text');form(root,h,c);action(root,live('Добавить доверенный сервер',()=>run(async()=>{await op('registry_update',{host:value(h),country:value(c),action:'trust'});await load()})))
  }
  if(S.registryTest)keyValue(root,[['Сервер отвечает',S.registryTest.server],['CPU',S.registryTest.cpu_load],['RAM',S.registryTest.memory_usage_percent],['VPN онлайн',S.registryTest.clients_online]]);
  section(root,'Фоновая служба Windows · аварийный резерв');
  root.append(notice('По умолчанию расписание выключено. PostgreSQL — основная база; автоматический экспорт Sheets возможен только после отдельной установки администратора и явного согласия. Служба работает после закрытия Helper.'));
  action(root,live('Проверить состояние задач',()=>run(async()=>{S.background=await op('background_status')}),'primary'));
  action(root,live('Открыть планировщик Windows',()=>run(()=>rpc('taskScheduler'))));
  if(S.background){const legacy=S.background.legacy||{},bg=S.background.v4||{};
   keyValue(root,[['Старая задача 3.x',legacy.verified?'Найдена':legacy.installed?'Есть receipt, не проверено':'Не подтверждена'],['Компонент 4.0',bg.component?'Установлен в Program Files':'Не установлен'],['Задание 4.0',bg.verified?'Обнаружено':bg.installed?'Есть receipt, не проверено':'Не подтверждено'],['Период, ч',bg.interval_hours],['Статус',bg.status],['Последняя проверка UTC',bg.checked_utc],['Успешно',bg.successful],['Ошибок',bg.failed]]);
   if(legacy.installed)root.append(notice('Сначала отключите прежнюю задачу в Helper 3.x. 4.0 не изменяет задание или файлы 3.x.'));
   if(!bg.component)root.append(notice('Нужен отдельный артефакт LinkVideo.Helper-4-Background-Setup из сборки. Обычный пользовательский установщик не создаёт SYSTEM-задачу.'));
   else{
    action(root,live('Проверить с правами администратора',()=>run(async()=>{await op('background_verify_admin');S.background=await op('background_status')})));
    const interval=field('Интервал резервирования','select',bg.interval_hours||12,[[6,'6 часов'],[12,'12 часов'],[24,'24 часа']]);form(root,interval);
    action(root,live('Выбрать JSON-ключ Sheets',()=>run(async()=>{S.sheets=await rpc('fileAction','sheets_load');S.background=await op('background_status')})));
    action(root,button(bg.installed?'Изменить расписание':'Включить аварийное расписание',()=>stage('background_preview',{action:'enable',interval:num(interval)}),'primary',!identity?.writes||!S.background.sheets_configured||legacy.installed));
    action(root,button('Запустить резервирование сейчас',()=>stage('background_preview',{action:'run'}),'quiet',!identity?.writes||!bg.installed||legacy.installed));
    action(root,button('Отключить задание 4.0',()=>stage('background_preview',{action:'disable'}),'quiet',!identity?.writes||!bg.installed));
   }
  }
  previewNotice(root,currentPlan(),out=>{if(out?.background&&S.background)S.background.v4=out.background});resultNote(root);

 }
 window.lvModules=Object.freeze({...(window.helper?.smokeMode?{fixture:(name,data)=>{if(!preview)throw Error('Fixture requires offline preview');Object.assign(S,data);if(name==='archives'||name==='diagnostics')S.archivePage=name;render(false)},refreshFixture:now=>{if(!preview)throw Error('Fixture requires offline preview');preserveScroll(()=>{applyClientRefresh(now);paintClient()})}}:{}),reset,enterPage,loadCreateHosts,discardPlan,client,selectedClient:()=>S.client,stopTraffic,stopRefresh,cloudRestore:c=>stage("cloud_restore_preview",{host:c.server,login:c.login}),dashboard,settings:workspaceSettings,infrastructure,infrastructureResults,lvLabel:host=>{const x=(S.lvFleet?.rows||[]).find(r=>r.server===host);return x&&!x.error?x.state_text:'Не проверено'},clearClient:()=>{stopTraffic();stopRefresh();S.selectedPort=null;S.expandedPorts=new Set();S.cameraMatchError='';S.newPorts=[];S.refreshWarning='';S.clientHistory=null;S.camPorts=null;S.client=null;S.plan=null;S.outcome=null},renderClient,render:(name,root)=>{({create,inactive,lv,recovery,admin,billing,archives:r=>archive(r,false),diagnostics:r=>archive(r,true),agent,camera,templates})[name]?.(root)}});
})();

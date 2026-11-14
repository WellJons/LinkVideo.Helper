'use strict';
(()=>{
 let S={};
 const reset=()=>{S={}};
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
 const run=async(fn)=>{const owner=identity;try{const result=await fn();if(identity===owner&&owner)render(false);return result}catch(err){if(identity===owner)error(err)}};
 const keyValue=(root,entries)=>{const d=el('div','kv-grid');for(const [k,v] of entries)d.append(add(el('div','kv'),el('small','',k),el('strong','',v==null||v===''?'—':Array.isArray(v)?v.join(', '):String(v))));root.append(d)};
 const status=v=>el('span',v?'online':'offline',v?'В сети':'Не в сети');
 const serverField=(title='VPN-сервер')=>field(title,'select',S.host||'',(S.hosts||[]).map(r=>[r.host,r.host+' · '+r.country]));
 const ensureHosts=(root)=>{if(S.hosts?.length)return true;root.append(notice('Загрузите список доступных серверов.'));action(root,live('Загрузить серверы',()=>run(async()=>{S.hosts=await rpc('servers');S.host=S.hosts[0]?.host||''}),'primary'));return false};
 const setHost=f=>{S.host=value(f);return S.host};
 const previewNotice=(root,plan,onConfirm)=>{if(!plan)return;const p=el('div','panel confirmation');p.append(el('p','eyebrow','ПОДТВЕРЖДЕНИЕ'),el('h3','',plan.label),el('p','','Одноразовое разрешение действует 120 секунд. Проверьте параметры до отправки.'));const b=live('Подтвердить',()=>run(async()=>{const token=plan.token;S.plan=null;try{S.outcome=await op('commit',{token});if(onConfirm)onConfirm(S.outcome)}finally{if(identity)render(false)}}),'primary');p.append(add(el('div','actionbar'),b,button('Отменить',()=>{S.plan=null;render(false)})));root.append(p)};
 const stage=(method,args)=>{const from=page;return run(async()=>{S.plan=null;S.outcome=null;S.plan=await op(method,args);S.planPage=from})};
 const currentPlan=()=>S.planPage===page?S.plan:null;
 const resultNote=(root)=>{if(S.outcome&&S.planPage===page)root.append(notice('Операция завершена. Проверьте актуальное состояние целевой записи.'))};
 function create(root){
  root.append(hero('VPN / PROVISIONING','Создание VPN-клиентов','Используется существующий распределитель адресов и портов RouterOS.'));
  if(!ensureHosts(root))return;
  const h=serverField(),l=field('Базовый логин','text',S.createLogin||''),p=field('Портов на учётку','number',S.createPorts||2),c=field('Количество учёток','number',S.createCount||1);
  p.input.min=1;p.input.max=14;c.input.min=1;c.input.max=20;
  form(root,h,l,p,c);action(root,live('Подготовить создание',()=>{S.createLogin=value(l);S.createPorts=num(p);S.createCount=num(c);stage('create_preview',{host:setHost(h),login:S.createLogin,ports:S.createPorts,count:S.createCount})},'primary'));
  previewNotice(root,currentPlan());resultNote(root);
  if(S.planPage==='create'&&S.outcome?.clients)root.append(table(['Логин','Сервер','VPN IP','Порты'],S.outcome.clients.map(c=>[c.login,c.server,c.remote_address,c.ports.join(', ')])));
 }
 async function client(host,login){S.client=null;S.plan=null;S.outcome=null;await run(async()=>{S.client=await op('client_detail',{host,login})})}
 function renderClient(root){
  const c=S.client;if(!c)return;
  section(root,'Карточка VPN · '+c.login);
  const panel=el('div','panel');keyValue(panel,[['Сервер',c.server],['VPN IP',c.remote_address],['Статус',c.is_online?'В сети':'Не в сети'],['Учётка',c.is_enabled?'Включена':'Отключена'],['Uptime',c.uptime],['RX, байт',c.rx_bytes],['TX, байт',c.tx_bytes]]);
  panel.append(table(['Порт','Статус','Конфликты'],c.ports.map(p=>[p,c.disabled_ports.includes(p)?'Выключен':c.active_ports.includes(p)?'Активен':'Настроен',c.port_conflicts?.[p]||'—'])));
  root.append(panel);
  action(root,live('Копировать VPN',()=>rpc('copyVpn',c.server,c.login).then(()=>toast('Конфигурация скопирована')).catch(error)));
  if(identity?.role==='sysadmin')action(root,live('WinBox',()=>run(()=>op('winbox',{host:c.server}))));
  const a=field('Действие','select','add',[['add','Добавить порты'],['disconnect','Разорвать сессию'],...(identity?.role==='sysadmin'?[['enable','Включить VPN'],['disable','Отключить VPN'],['port_enable','Включить порт'],['port_disable','Отключить порт'],['remove_port','Удалить порт'],['recreate_port','Пересоздать порт'],['password','Сменить пароль']]:[])]),p=field('Порт / количество','number',1),pw=field('Новый пароль','password');
  form(root,a,p,pw);action(root,live('Подготовить действие',()=>{const args={host:c.server,login:c.login,action:value(a),count:num(p),port:num(p),password:pw.input.value};pw.input.value='';stage('vpn_preview',args)},'primary'));action(root,live('Обновить карточку',()=>client(c.server,c.login)));
  if(identity?.role==='sysadmin'){const d=field('Для удаления: точный server/login','text');form(root,d);action(root,live('Подготовить удаление',()=>stage('delete_preview',{host:c.server,login:c.login,confirm:value(d)})))}
  previewNotice(root,currentPlan(),out=>{if(out?.login)S.client=out;else if(out?.deleted){S.client=null;searchState=null}});resultNote(root);
 }
 function inactive(root){
  root.append(hero('VPN / LIFECYCLE','Неактивные VPN','Жизненный цикл учёток, состояния LV и дата последнего отключения.'));
  if(!ensureHosts(root))return;
  const h=serverField(),d=field('Порог неактивности, дней','number',S.inactiveDays||30);
  form(root,h,d);action(root,live('Проверить',()=>run(async()=>{S.inactiveDays=num(d);S.inactive=await op('inactive',{host:setHost(h),days:S.inactiveDays})}),'primary'));
  if(S.inactive){root.append(notice('Найдено: '+S.inactive.length));root.append(table(['Логин','Состояние LV','Последний выход','VPN IP','Статус'],S.inactive.map(c=>[c.login,c.lifecycle_state,c.last_logged_out||'—',c.remote_address,status(c.is_online)])))}
 }
 function lv(root){
  root.append(hero('VPN / AUTOMATION','LV-автоматика','Состояние скриптов, планировщиков и жизненного цикла.'));
  if(!preview&&identity?.role!=='sysadmin'){root.append(notice('Раздел доступен системному администратору.'));return}
  if(!ensureHosts(root))return;
  const h=serverField(),a=field('Действие','select','install',[['install','Установить / обновить'],['start','Запустить'],['pause','Приостановить'],['seed','Инициализировать'],['quarantine_on','Включить карантин'],['quarantine_off','Выключить карантин']]);
  form(root,h,a);action(root,live('Проверить состояние',()=>run(async()=>{S.lv=await op('lv_status',{host:setHost(h)})}),'primary'));action(root,live('Подготовить изменение',()=>stage('lv_preview',{host:setHost(h),action:value(a)})));
  const x=S.lv;if(x)keyValue(root,[['Сервер',x.server],['Версия',x.version],['Скрипты',x.scripts_installed+' / '+x.scripts_expected],['Планировщики',x.schedulers_installed+' / '+x.schedulers_expected],['Установка',x.installed?'Подтверждена':'Не полная'],['Пауза',x.paused?'Да':'Нет'],['Активные',x.active],['Сон',x.sleeping],['Карантин',x.quarantine],['Архив',x.archive],['Неизвестные',x.unknown]]);
  previewNotice(root,currentPlan(),out=>S.lv=out);resultNote(root);
 }
 function admin(root){
  root.append(hero('HELPER / WORK ACCOUNTS','CRM / B2O','Две независимые корпоративные сессии. Данные MikroTik автоматически не используются.'));
  const u=field('Логин админки','text',S.admin?.username||''),p=field('Пароль','password'),t=field('Подключение','select','both',[['both','CRM и B2O'],['crm','Только CRM'],['b2o','Только B2O']]);
  const remember=field('Запомнить вход (Windows)','select','no',[['no','Нет'],['yes','Да']]);form(root,u,p,t,remember);action(root,live('Подключить',()=>{const password=p.input.value;p.input.value='';run(async()=>{S.billingClient=null;S.hits=null;S.archive=null;S.plan=null;S.admin=await rpc('adminLogin',value(u),password,value(t),value(remember)==='yes')})},'primary'));
  action(root,live('Вход из хранилища',()=>run(async()=>{S.admin=await rpc('adminSaved');S.billingClient=null;S.hits=null;S.archive=null;S.plan=null})));
  action(root,live('Очистить сохранённый вход',()=>run(()=>rpc('clearSaved','admin'))));
  action(root,live('Проверить сессии',()=>run(async()=>{S.admin=await op('admin_status')})));
  action(root,live('Выйти из админки',()=>run(async()=>{S.admin=await op('admin_logout');S.billingClient=null;S.hits=null;S.archive=null;S.plan=null})));
  if(S.admin){keyValue(root,[['Аккаунт',S.admin.username],['CRM',S.admin.crm?'Подключено':'Не подключено'],['B2O',S.admin.b2o?'Подключено':'Не подключено']]);for(const e of S.admin.errors||[])root.append(notice(e))}
 }
 function billing(root){
  root.append(hero('CRM / BILLING','Биллинг','Поиск клиента, карточка, история камеры и тарифный справочник BY / KZ.'));
  const q=field('Логин клиента','text',S.billingQuery||'');
  form(root,q);action(root,live('Найти',()=>run(async()=>{S.billingQuery=value(q);S.billingClient=null;S.history=null;S.catalog=null;S.plan=null;S.hits=await op('billing_search',{query:S.billingQuery})}),'primary'));
  action(root,live('Все тарифы',()=>run(async()=>{S.catalog=await op('billing_catalog',{scope:'all'})})));
  if(S.hits)root.append(table(['Логин','Клиент','Страна','Баланс',''],S.hits.map(x=>[x.login,x.account_name,x.country,x.balance_text,live('Открыть',()=>run(async()=>{S.billingClient=await op('billing_select',{id:x.id});S.plan=null;S.history=null;S.catalog=null}))])));
  const c=S.billingClient;if(c){section(root,c.display_name||c.login);keyValue(root,[['Логин',c.login],['Страна',c.country],['Статус',c.status],['Баланс',c.balance_text],['Договор',c.contract_number],['Тип',c.legal_type]]);
   root.append(table(['Камера','ID','Тариф','Статус','Стоимость',''],c.services.map(x=>[x.camera_name,x.camera_id,x.tariff_name,x.status,x.total_text,live('История',()=>run(async()=>{S.history=await op('billing_history',{camera:x.id})}))])));
   action(root,live('Тарифы клиента',()=>run(async()=>{S.catalog=await op('billing_catalog',{scope:'client'})})));
   if(c.services.length){const cam=field('Камера','select','',c.services.map(x=>[x.id,x.camera_name+' · '+x.id])),a=field('Изменение','select','tariff',[['tariff','Сменить тариф'],['status','Изменить статус']]),t=field('ID тарифа','text'),st=field('Статус','select','1',[[1,'Включить'],[0,'Отключить']]),date=field('Дата','date'),comment=field('Комментарий','text');form(root,cam,a,t,st,date,comment);action(root,live('Подготовить изменение',()=>stage('billing_preview',{camera:value(cam),action:value(a),tariff:value(t),status:num(st),date:value(date),comment:value(comment)}),'primary'))}
  }
  if(c?.services.length){const recipient=field('Логин получателя','text',S.recipientQuery||'');form(root,recipient);action(root,live('Найти получателя CRM',()=>run(async()=>{S.recipientQuery=value(recipient);S.recipients=await op('billing_recipient',{query:S.recipientQuery})})));if(S.recipients?.length){const to=field('Получатель','select','',S.recipients.map(r=>[r.id,r.login+' · '+r.country+' · '+r.account_name])),camera=field('Камера для переноса','select','',c.services.map(x=>[x.id,x.camera_name+' · '+x.id]));form(root,to,camera);action(root,live('Подготовить перенос',()=>stage('billing_transfer_preview',{recipient:value(to),camera:value(camera)}))) }}
  if(S.catalog){section(root,'Каталог тарифов');root.append(table(['ID','Название','Страна','Тип','Цена'],(S.catalog.tariffs||[]).map(t=>[t.id,t.name,t.country,t.account_type,t.price])))}
  if(S.history){section(root,'История камеры');root.append(table(['Дата','Пользователь','Действие','Поле','До','После'],S.history.map(h=>[h.date,h.user,h.action,h.field_name,h.old_value,h.new_value])))}
  previewNotice(root,currentPlan(),out=>S.billingClient=out);resultNote(root);
 }
 function archive(root,diagnose){
  root.append(hero(diagnose?'ARCHIVE / DIAGNOSIS':'ARCHIVE / DOWNLOAD',diagnose?'Диагностика архива':'Архивы','Период задаётся в локальном времени камеры. Источник — существующий DVR / B2O сервис.'));
  const saved=S.archiveForm||{},cam=field('ID камеры','text',saved.camera||''),country=field('Регион','select',saved.operator||241,[[241,'Россия'],[1721,'Казахстан'],[1741,'Беларусь']]),start=field('Начало','datetime-local',saved.start||''),end=field('Конец','datetime-local',saved.end||''),compare=field('Камеры того же адреса, через запятую','text',saved.compare||''),server=field('Камеры того же сервера, через запятую','text',saved.server_compare||'');
  form(root,cam,country,start,end,...(diagnose?[compare,server]:[]));
  action(root,live(diagnose?'Выполнить диагностику':'Найти архив',()=>run(async()=>{S.archive=null;S.archivePage=page;S.archiveForm={camera:value(cam),operator:num(country),start:value(start),end:value(end),compare:value(compare),server_compare:value(server)};const a={...S.archiveForm,compare:value(compare).split(',').map(s=>s.trim()).filter(Boolean),server_compare:value(server).split(',').map(s=>s.trim()).filter(Boolean)};S.archive=await op(diagnose?'archive_diagnose':'archive_discover',a)}),'primary'));
  const d=S.archive;if(d&&S.archivePage===page){const time=t=>new Date((t+d.timezone*3600)*1000).toISOString().slice(0,19).replace('T',' ');
   keyValue(root,[['Камера',d.camera],['Сервер',d.server],['Часовой пояс','UTC '+d.timezone],['Покрытие',Math.round(d.coverage*10)/10+'%'],['Пропуски',d.gaps.length],['Проверено серверов',d.checked],['Ошибки',d.error_count]]);
   if(!d.exact_timeline)root.append(notice('Точный DVR timeline не подтверждён; HLS не доказывает отсутствие разрывов.'));
   const timeline=el('div','timeline');for(const s of d.slices)timeline.append(add(el('span','slice'),el('small','',time(s.start)+' — '+time(s.end))));root.append(timeline);
   if(d.gaps.length){section(root,'Пропуски');root.append(table(['Начало','Конец'],d.gaps.map(g=>[time(g.start),time(g.end)])))}
   if(d.diagnosis){section(root,d.diagnosis.title);root.append(add(el('div','panel'),el('h3','',d.diagnosis.side+' · '+d.diagnosis.confidence),el('p','',d.diagnosis.summary),...d.diagnosis.cautions.map(c=>el('p','',c))))}
   if(!diagnose&&d.downloadable){const method=field('Метод скачивания','select','ffmpeg',[['ffmpeg','FFmpeg со звуком'],['ffmpeg_no_audio','FFmpeg без звука'],['curl','Прямой MP4 / curl']]);form(root,method);action(root,live('Сохранить MP4',()=>run(async()=>{const r=await rpc('archiveDownload',value(method));toast((r.partial?'Частично сохранён: ':'Сохранён: ')+r.output)}),'primary'))}
  }
 }
 function agent(root){
  root.append(hero('CAMERA / AGENT','Новый Агент','Поиск камеры и настройки времени, ориентации, битрейта через исходный AgentService.'));
  const sn=field('Серийный номер','text',S.serial||'');form(root,sn);
  action(root,live('Найти камеру',()=>run(async()=>{S.serial=value(sn);S.agent=await op('agent_search',{serial:S.serial});S.plan=null}),'primary'));
  const c=S.agent;if(c){keyValue(root,[['SN',c.serial_number],['Модель',c.model],['Производитель',c.vendor],['Сервер',c.server],['Версия агента',c.version]]);
   const a=field('Настройка','select','timezone',[['timezone','Часовой пояс (часы)'],['rotation','Поворот (градусы)'],['bitrate','Битрейт (Кбит/с)']]),v=field('Значение','number',7);form(root,a,v);action(root,live('Подготовить изменение',()=>stage('agent_preview',{action:value(a),value:num(v)}),'primary'))}
  previewNotice(root,currentPlan());resultNote(root);
 }
 function templates(root){
  root.append(hero('SUPPORT / KNOWLEDGE','Шаблоны ответов','Личная библиотека быстрых ответов — локальная SQLite база 4.0.'));
  const q=field('Поиск','text',S.templateQuery||''),cat=field('Категория','select',S.templateCategory||'',[['','Все'],...(S.templates?.categories||[]).map(v=>[v,v])]);form(root,q,cat);
  const load=async()=>{S.templateQuery=value(q);S.templateCategory=value(cat);S.templates=await op('templates_list',{query:S.templateQuery,category:S.templateCategory})};
  action(root,live('Загрузить шаблоны',()=>run(load),'primary'));action(root,live('Новый шаблон',()=>{S.editTemplate={};render(false)}));
  action(root,live('Импорт DOCX',()=>run(async()=>{const r=await rpc('fileAction','templates_import',{category:value(cat)});await load();toast('Импортировано: '+r.count)})));
  if(S.templates)for(const t of S.templates.items){const row=el('article','panel template');row.append(el('h3','',t.title),el('small','',t.category+(t.favorite?' · ★':'')),el('p','',t.body),add(el('div','actionbar'),live('Копировать',()=>rpc('copy',t.body).then(()=>toast('Скопировано')).catch(error)),live('Редактировать',()=>{S.editTemplate=t;render(false)}),live('Изображения',()=>run(async()=>{S.imageTemplate=t.id;S.images=await op('templates_images',{id:t.id})})),live('Прикрепить',()=>run(async()=>{await rpc('fileAction','templates_attach',{id:t.id});S.imageTemplate=t.id;S.images=await op('templates_images',{id:t.id})})),live(t.favorite?'Убрать из избранного':'В избранное',()=>run(async()=>{await op('templates_favorite',{id:t.id,selected:!t.favorite});await load()})),live('Удалить',()=>{if(confirm('Удалить локальный шаблон?'))run(async()=>{await op('templates_delete',{id:t.id});S.editTemplate=null;await load()})})));root.append(row)}
  if(S.imageTemplate){section(root,'Изображения шаблона');for(const img of S.images||[]){const im=el('img','template-image');if(/^data:image\/(png|jpeg|webp|gif);base64,/.test(img.src))im.src=img.src;im.alt='Вложение';root.append(add(el('div','panel'),im,live('Открепить',()=>{if(confirm('Убрать изображение из шаблона?'))run(async()=>{await op('templates_detach',{id:S.imageTemplate,image:img.id});S.images=await op('templates_images',{id:S.imageTemplate})})})))}}
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
  action(root,live('Подготовить экспорт в Sheets',()=>stage('sheets_preview',{host:setHost(h)})));
  if(S.deleted)root.append(table(['Логин','Сервер','VPN IP','Удалён','Порты',''],S.deleted.map(r=>[r.login,r.server,r.remote_address,r.deleted_at,r.ports,live('Восстановить',()=>stage('restore_preview',{host:r.server,login:r.login}))])));
  previewNotice(root,currentPlan());resultNote(root);
 }
 function activity(root){
  root.append(hero('VPN / ACTIVITY','История VPN','Отдельное подключение к VPNSync. Источник — существующий CloudVPNSyncClient.'));
  if(!preview&&identity?.role!=='sysadmin'){root.append(notice('Доступно системному администратору.'));return}
  const host=field('DNS или IPv4','text',S.cloudHost||''),port=field('Порт','number',S.cloudPort||443),u=field('Логин облака','text'),pw=field('Пароль облака','password'),scheme=field('Протокол','select','https',[['https','HTTPS'],['http','HTTP в доверенной сети']]);form(root,host,port,u,pw,scheme);
  action(root,live('Подключить',()=>{const password=pw.input.value;pw.input.value='';if(value(scheme)==='http'&&!confirm('HTTP не защищает пароль при передаче. Продолжить только в доверенной сети?'))return;run(async()=>{S.cloudHost=value(host);S.cloudPort=num(port);S.cloud=await op('cloud_connect',{host:S.cloudHost,port:S.cloudPort,username:value(u),password,tls:value(scheme)==='https',allow_http:value(scheme)==='http'})})},'primary'));
  if(S.cloud?.connected)root.append(notice('Подключение подтверждено.'));
  const login=field('Фильтр логина','text',S.activityLogin||''),source=field('Источник','text',S.activitySource||'');form(root,login,source);
  action(root,live('Обновить историю',()=>run(async()=>{S.activityLogin=value(login);S.activitySource=value(source);S.historyVpn=await op('activity',{login:S.activityLogin,source:S.activitySource})}),'primary'));
  if(S.historyVpn)root.append(table(['Время','Событие','Сервер','Логин','Источник','Оператор'],S.historyVpn.map(r=>[r.created_at||r.timestamp,r.action,r.server,r.login,r.source,r.username||r.actor])));
 }
 function camera(root){
  root.append(hero('B2O / CAMERAS','Камеры и клиенты B2O','Проверка RTSP/TCP, сведения об устройстве, чтение и создание корпоративной учётки.'));
  const id=field('ID камеры','text',S.cameraId||''),opid=field('Регион','select','241',[[241,'Россия'],[1721,'Казахстан'],[1741,'Беларусь']]);form(root,id,opid);
  action(root,live('Диагностика камеры',()=>run(async()=>{S.cameraId=value(id);S.cameraInfo=await op('camera_inspect',{camera:S.cameraId,operator:num(opid)})}),'primary'));
  const c=S.cameraInfo;if(c)keyValue(root,[['Камера',c.name||c.camera_id],['Сервер',c.server],['IP',c.ip],['RTSP-порт',c.rtsp_port],['Ping',c.ping_text],['TCP',c.tcp_text],['Состояние',c.status],['Модель',c.model],['SN',c.serial],['Архив',c.archive_depth],['Последний поток',c.last_stream]]);
  section(root,'Клиент B2O');const country=field('Страна','select','Россия',[['Россия','Россия'],['Казахстан','Казахстан'],['Беларусь','Беларусь']]),account=field('ID аккаунта','number');form(root,country,account);
  action(root,live('Проверить клиента',()=>run(async()=>{S.b2oClient=await op('b2o_read',{country:value(country),id:num(account)})})));
  if(S.b2oClient)keyValue(root,[['ID',S.b2oClient.id_account],['Логин',S.b2oClient.login],['Имя',S.b2oClient.client_name],['Тип',S.b2oClient.account_type]]);
  section(root,'Создание обычного аккаунта с биллингом');const name=field('Имя клиента'),login=field('Логин'),password=field('Пароль','password'),phone=field('Телефон');form(root,name,login,password,phone);action(root,live('Подготовить создание',()=>{const pw=password.input.value;password.input.value='';stage('b2o_create_preview',{country:value(country),name:value(name),login:value(login),password:pw,phone:value(phone)})},'primary'));
  previewNotice(root,currentPlan());if(S.outcome?.id_account&&S.planPage===page)keyValue(root,[['Создан ID',S.outcome.id_account],['Проверка чтением',S.outcome.read_back_verified?'Да':'Не подтверждена']]);resultNote(root);
 }
 function dashboard(root){
  section(root,'Рабочие инструменты');const g=el('div','dashboard-grid');
  for(const [id,desc] of [['create','Создание учёток и NAT'],['inactive','Жизненный цикл VPN'],['lv','Скрипты и автоматизация'],['recovery','FULL backup · восстановление'],['activity','События инфраструктуры'],['billing','Тарифы и услуги'],['archives','Поиск и экспорт MP4'],['diagnostics','Разрывы и сравнение'],['camera','B2O и сетевые проверки'],['agent','Настройки камеры'],['templates','Библиотека ответов'],['admin','Независимые сессии']]){
   const p=pages.find(x=>x[0]===id),b=btn('','dash-tile',()=>go(id));b.append(navIcon(id,p[1]),add(el('div'),el('strong','',p[2]),el('small','',desc)),el('span','dash-arrow','›'));g.append(b)
  }
  root.append(g);
 }
 function workspaceSettings(root){
  if(preview){root.append(notice('Настройки корпоративных серверов доступны после входа.'));return}
  section(root,'Защищённые учётные данные');action(root,live('Очистить сохранённый вход',()=>run(()=>rpc('clearSaved','work'))));
  if(identity?.role!=='sysadmin')return;
  section(root,'Реестр VPN-серверов');
  const load=async()=>{S.registry=await op('registry_list');S.hosts=await rpc('servers');serverState=null;searchState=null};
  action(root,live('Открыть реестр',()=>run(load),'primary'));
  if(S.registry){root.append(table(['Адрес','Регион','Доступ',''],S.registry.map(r=>[r.host,r.country,r.enabled?(r.trusted?'Активен':'Требуется доверие'):'Выключен',add(el('div','actionbar'),live(r.enabled?'Выключить':'Включить',()=>run(async()=>{await op('registry_update',{host:r.host,action:r.enabled?'disable':'enable'});await load()})),...(!r.builtin?[live('Доверять',()=>run(async()=>{await op('registry_update',{host:r.host,action:'trust',country:r.country});await load()})),live('Удалить',()=>run(async()=>{await op('registry_update',{host:r.host,action:'remove'});await load()}))]:[]))])));
   const h=field('Дополнительный DNS/IPv4','text'),c=field('Регион','text');form(root,h,c);action(root,live('Добавить доверенный сервер',()=>run(async()=>{await op('registry_update',{host:value(h),country:value(c),action:'trust'});await load()})))
  }
  section(root,'Фоновая служба Windows 3.x');
  action(root,live('Проверить существующее задание',()=>run(async()=>{S.background=await op('background_status')})));
  if(S.background)keyValue(root,[['Установлено',S.background.installed?'Да':'Нет'],['Период, ч',S.background.interval_hours],['Статус',S.background.status]]);
  root.append(notice('4.0 не создаёт вторую системную задачу параллельно с 3.x. Автоматический запуск 4.0 требует отдельной защищённой миграции.'))
 }
 window.lvModules=Object.freeze({reset,client,dashboard,settings:workspaceSettings,clearClient:()=>{S.client=null;S.plan=null;S.outcome=null},renderClient,render:(name,root)=>{({create,inactive,lv,recovery,activity,admin,billing,archives:r=>archive(r,false),diagnostics:r=>archive(r,true),agent,camera,templates})[name]?.(root)}});
})();

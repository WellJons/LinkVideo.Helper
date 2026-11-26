'use strict';
const $=id=>document.getElementById(id);
// Primary routes follow MainWindow.NAV_ITEMS in desktop 3.0.27, not the web workspace.
const pages=[['create','＋','Создание клиента'],['search','⌕','Поиск и управление'],['archives','↓','Скачать архив'],['diagnostics','◇','Диагностика архива'],['billing','₽','Клиенты и тарифы'],['agent','◉','Новый Агент'],['templates','▤','Шаблоны ответов'],['servers','▦','VPN-серверы'],['inactive','⌛','VPN-клиенты'],['settings','⚙','Настройки'],['camera','◉','Камеры / B2O'],['admin','♙','CRM / B2O'],['lv','⚡','LV-автоматика'],['recovery','↺','Резерв / восстановление'],['home','⌂','Обзор']];
const secondaryRoutes=Object.freeze({camera:'search',admin:'billing',lv:'servers',recovery:'servers'});
const primaryPages=pages.filter(([id])=>id!=='home'&&!secondaryRoutes[id]);
const primaryRoute=id=>secondaryRoutes[id]||id;
let identity=null,preview=false,page='create',epoch=0,toastTimer,searchState=null,serverState=null; let taskBusy=false,idleRead=null;
async function backgroundRead(method,args){if(taskBusy||idleRead)return null;const p=window.helper.operation(method,args);idleRead=p;try{return await p}finally{if(idleRead===p)idleRead=null}}
const preference=(key,fallback)=>{try{return localStorage.getItem(key)||fallback}catch{return fallback}};
const save=(key,value)=>{try{localStorage.setItem(key,value)}catch{}};
const themes=[['dark','Графитовая 4.0',false],['light','Светлая 4.0',true],['terminal_code','Terminal // Code',false],['rose_milk','Розовое молочко',true],['linkvideo_2026','LinkVideo · оранжевая',true],['lavender_mist','Лавандовая',true],['ocean_blue','Тёмно-синяя',false],['midnight_purple','Полуночная',false],['cherry_pink','Тёмная вишня',false],['soft_gray','Графитовая 3.x',false]];
let theme=preference('lv4-theme','dark'),motion=preference('lv4-motion','on')!=='off';
if(!themes.some(x=>x[0]===theme))theme='dark';
const isLight=()=>!!themes.find(x=>x[0]===theme)?.[2];
const reduced=matchMedia('(prefers-reduced-motion: reduce)');
function preferences(){document.documentElement.classList.toggle('light',isLight());document.documentElement.dataset.theme=theme;document.documentElement.classList.toggle('reduce-motion',!motion||reduced.matches);$('theme').textContent=isLight()?'Тёмная тема':'Светлая тема';if(!motion||reduced.matches)for(const animation of document.getAnimations())animation.cancel()}
preferences();reduced.addEventListener('change',preferences);
function setTheme(next){if(!themes.some(x=>x[0]===next))return;theme=next;save('lv4-theme',theme);preferences();if(page==='settings'&&identity)render(false)}
function toggleTheme(){setTheme(isLight()?'dark':'light')}
$('theme').onclick=toggleTheme;
function el(tag,cls='',text=''){const e=document.createElement(tag);if(cls)e.className=cls;if(text!==''&&text!=null)e.textContent=String(text);return e}
function add(parent,...nodes){parent.append(...nodes);return parent}
function btn(text,cls,fn){const e=el('button',cls,text);e.type='button';e.onclick=fn;return e}
function hero(k,title,desc){return add(el('section','hero'),el('p','eyebrow',k),el('h1','',title),el('p','',desc))}
function notice(text){return el('div','notice',text)}
function busy(text){return add(el('div','notice'),el('span','loading'),el('span','',text))}
function toast(text){$('toast').textContent=text;$('toast').hidden=false;const node=$('toast');node.getAnimations().forEach(a=>a.cancel());if(motion&&!reduced.matches)node.animate([{opacity:0,transform:'translateY(5px)'},{opacity:1,transform:'translateY(0)'}],{duration:140,easing:'ease-out'});clearTimeout(toastTimer);toastTimer=setTimeout(()=>$('toast').hidden=true,4000)}
function taskUI(active,label='Выполняется операция…',cancelable=true){
 $('global-task').hidden=!active;$('workspace').classList.toggle('task-active',active);$('page').setAttribute('aria-busy',String(active));$('task-label').textContent=label;$('task-progress').removeAttribute('value');$('cancel-task').disabled=!cancelable
}
async function rpc(method,...args){
 if(!window.helper)throw Error('Запустите интерфейс через Electron.');
 const tracked=!['logout','cancel','copy'].includes(method);
 // Claim the foreground slot before waiting for a background thumbnail.
 if(tracked&&taskBusy)throw Error('Дождитесь завершения текущей операции.');
 if(tracked){taskBusy=true;taskUI(true,idleRead?'Завершаю фоновое чтение…':'Выполняется операция…',!idleRead)}
 try{
  if(tracked&&idleRead)await idleRead.catch(()=>{});
  if(tracked)taskUI(true);
  const result=await window.helper[method](...args);if(!result.ok)throw Error(result.error);return result.data
 }finally{if(tracked){taskBusy=false;taskUI(false)}}
}
$('cancel-task').onclick=async()=>{try{await rpc('cancel');$('task-label').textContent='Запрошена отмена. Ожидаю завершения…';$('cancel-task').disabled=true}catch(err){toast(err.message)}};
if(window.helper?.onProgress)window.helper.onProgress(p=>{if(taskBusy&&!$('cancel-task').disabled&&typeof p.label==='string'){$('task-label').textContent=p.label+(typeof p.value==='number'?' · '+Math.round(p.value)+'%':'');if(typeof p.value==='number')$('task-progress').value=Math.min(100,Math.max(0,p.value));else $('task-progress').removeAttribute('value')}});

function enter(user,isPreview=false){identity=user;preview=isPreview;settingsTab='appearance';if(window.lvModules)window.lvModules.reset();searchState=null;serverState=null;$('login').hidden=true;$('workspace').hidden=false;$('account').textContent=user.username;$('account').title=user.username;$('role').textContent=preview?'Без подключения':user.role==='employee'?'Техподдержка':'Системный администратор';$('session-dot').classList.toggle('preview',preview);go('create')}
$('login-form').onsubmit=async e=>{e.preventDefault();const button=$('sign-in');button.disabled=true;$('preview').disabled=true;button.textContent='Проверка учётной записи…';$('login-error').textContent='';let password=$('password').value;$('password').value='';try{const result=await rpc('login',$('username').value,password,$('remember-work').checked);enter(result);void window.lvModules?.loadCreateHosts();if($('remember-work').checked&&!result.remembered)toast('Не удалось сохранить вход через шифрование Windows.')}catch(err){$('login-error').textContent=err.message}finally{password='';button.disabled=false;$('preview').disabled=false;button.textContent='Войти в рабочую область'}};
async function savedStatus(){if(!window.helper?.rememberStatus)return;try{const s=await rpc('rememberStatus');$('saved-work').hidden=!s.work;$('remember-work').disabled=!s.available}catch{}}
$('saved-work').onclick=async()=>{const b=$('saved-work');b.disabled=true;$('sign-in').disabled=true;$('preview').disabled=true;try{enter(await rpc('loginSaved'));void window.lvModules?.loadCreateHosts()}catch(err){$('login-error').textContent=err.message;await savedStatus()}finally{b.disabled=false;$('sign-in').disabled=false;$('preview').disabled=false}};
void savedStatus();
$('preview').onclick=()=>enter({username:'Демонстрация',role:'preview'},true);
$('logout').onclick=async()=>{if(taskBusy){toast('Сначала завершите текущую операцию.');return}if(window.lvModules)window.lvModules.reset();epoch++;identity=null;searchState=null;serverState=null;$('workspace').hidden=true;$('login').hidden=false;$('page').replaceChildren();$('password').value='';$('login-error').textContent='';$('toast').hidden=true;$('sign-in').disabled=true;$('preview').disabled=true;try{if(idleRead)await idleRead.catch(()=>{});if(!preview)await rpc('logout')}catch{}finally{$('sign-in').disabled=false;$('preview').disabled=false;$('username').focus();void savedStatus()}};
const svgs={home:'M3 10 12 3l9 7v10a1 1 0 0 1-1 1h-5v-7H9v7H4a1 1 0 0 1-1-1z',search:'M21 21l-5-5M10.5 18a7.5 7.5 0 1 0 0-15 7.5 7.5 0 0 0 0 15z',servers:'M4 3h16v7H4z M4 14h16v7H4z M7 6h.1 M7 17h.1',create:'M12 5v14M5 12h14',inactive:'M12 8v5l3 2M12 22a10 10 0 1 0 0-20 10 10 0 0 0 0 20z',lv:'M13 2 4 14h7l-1 8 10-12h-7z',recovery:'M3 12a9 9 0 1 1 3 7M3 4v8h8',activity:'M4 19V5M4 19h17M8 14l4-5 4 3 4-6',billing:'M7 3h7a5 5 0 0 1 0 10H7m0-10v18m-3-5h12',archives:'M3 4h18v6H3zM5 10v11h14V10M10 14h4',diagnostics:'M3 12h4l3-7 4 14 3-7h4',agent:'M4 5h12v14H4zM16 9l5-3v12l-5-3',camera:'M3 6h5l2-3h4l2 3h5v14H3zM12 16a4 4 0 1 0 0-8 4 4 0 0 0 0 8z',templates:'M4 3h16v18H4zM8 8h8M8 12h8M8 16h4',admin:'M12 13a5 5 0 1 0 0-10 5 5 0 0 0 0 10zM3 22v-2a7 7 0 0 1 7-7h4a7 7 0 0 1 7 7v2',settings:'M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8zM12 2v3M12 19v3M2 12h3M19 12h3M5 5l2 2m10 10 2 2M19 5l-2 2M7 17l-2 2'};
function navIcon(id,icon){const s=document.createElementNS('http://www.w3.org/2000/svg','svg');s.setAttribute('viewBox','0 0 24 24');s.setAttribute('class','nav-icon');s.setAttribute('aria-hidden','true');const p=document.createElementNS(s.namespaceURI,'path');p.setAttribute('d',svgs[id]||svgs.home);s.append(p);return s}
function commands(){const dialog=$('command-dialog');if(!identity||dialog.open)return;dialog.showModal();$('command-query').value='';commandResults();$('command-query').focus()}
function commandResults(){const q=$('command-query').value.toLowerCase().trim();const target=$('command-results');target.replaceChildren();for(const [id,icon,label] of pages.filter(x=>pageAllowed(x[0])&&(!q||x[2].toLowerCase().includes(q)))){const b=btn('','command-row',()=>{$('command-dialog').close();go(id)});b.append(navIcon(id,icon),el('span','',label));target.append(b)}}
$('command-open').onclick=commands;$('command-query').oninput=commandResults;$('command-close').onclick=()=>$('command-dialog').close();
document.addEventListener('keydown',e=>{if((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==='k'){e.preventDefault();commands()}if($('command-dialog').open&&e.key==='Enter'&&document.activeElement===$('command-query')){$('command-results').querySelector('button')?.click();e.preventDefault()}if($('command-dialog').open&&['ArrowDown','ArrowUp'].includes(e.key)){const rows=[...$('command-results').querySelectorAll('button')];if(!rows.length)return;e.preventDefault();const i=rows.indexOf(document.activeElement);rows[e.key==='ArrowDown'?(i+1)%rows.length:(i<=0?rows.length-1:i-1)].focus()}});
const adminPages=new Set(['servers','inactive','lv','recovery']);
const pageAllowed=id=>preview||identity?.role==='sysadmin'||!adminPages.has(id);
function go(next){if(!pages.some(p=>p[0]===next&&!p[3])||!pageAllowed(next))return;if(next!==page)window.lvModules?.discardPlan();if(next!=='search'){window.lvModules?.stopTraffic();window.lvModules?.stopRefresh()}page=next;epoch++;render(true);window.lvModules?.enterPage(next)}
// Additional tools are kept inside their original desktop workspace.
let navRole=null;
// Keep the viewport, independent client panes and nested table positions
// across both full renders and the targeted 15-second VPN refresh.
function scrollSnapshot(){
 const root=$('page'),nodes=[...root.querySelectorAll('[data-scroll-key],.table-wrap')],counts=new Map();
 const entries=nodes.map(n=>{const base=n.dataset.scrollKey||'table:'+n.querySelector('thead')?.textContent;const i=counts.get(base)||0;counts.set(base,i+1);return [base+'#'+i,n.scrollLeft,n.scrollTop]});
 const doc=document.scrollingElement;return {x:doc.scrollLeft,y:doc.scrollTop,entries};
}
function restoreScroll(snapshot){
 const counts=new Map(),saved=new Map(snapshot.entries.map(([k,x,y])=>[k,[x,y]]));
 for(const n of $('page').querySelectorAll('[data-scroll-key],.table-wrap')){const base=n.dataset.scrollKey||'table:'+n.querySelector('thead')?.textContent;const i=counts.get(base)||0;counts.set(base,i+1);const pos=saved.get(base+'#'+i);if(pos){n.scrollLeft=pos[0];n.scrollTop=pos[1]}}
 window.scrollTo({left:snapshot.x,top:snapshot.y,behavior:'instant'});
}
function preserveScroll(fn){const snapshot=scrollSnapshot();try{return fn()}finally{restoreScroll(snapshot)}}
function controlKey(node){if(!node)return '';if(node.id)return 'id:'+node.id;for(const name of ['portSelect','themeChoice','billingTab','settingsTab','agentAction','templateId','sectionRoute','searchMode','createControl','createAction'])if(node.dataset?.[name]!=null)return name+':'+node.dataset[name];return ''}
function render(animate){
 const nav=$('nav'),key=(preview?'preview':identity?.role)||'none';
 if(navRole!==key||!nav.children.length){
  navRole=key;nav.replaceChildren();
  for(const [id,icon,label,soon] of primaryPages){if(!pageAllowed(id))continue;const b=btn('','',()=>{if(page!==id)go(id)});b.dataset.page=id;b.title=label;b.setAttribute('aria-label',label);add(b,navIcon(id,icon),el('span','',label));if(soon){b.disabled=true;add(b,el('span','soon','СКОРО'))}nav.append(b)}
 }
 for(const b of nav.querySelectorAll('button[data-page]')){if(b.dataset.page===primaryRoute(page))b.setAttribute('aria-current','page');else b.removeAttribute('aria-current')}
 const active=nav.querySelector('[aria-current=page]');if(animate&&active){const a=active.getBoundingClientRect(),n=nav.getBoundingClientRect();if(a.top<n.top)nav.scrollTop+=a.top-n.top;else if(a.bottom>n.bottom)nav.scrollTop+=a.bottom-n.bottom}
 $('location').textContent=pages.find(p=>p[0]===page)[2];
 const root=$('page');for(const a of root.getAnimations({subtree:true}))a.cancel();
 const focus=document.activeElement,controls=[...root.querySelectorAll('input,select,textarea,button')],idx=controls.indexOf(focus),tag=focus?.tagName,type=focus?.type,caption=focus?.closest('label')?.textContent||focus?.textContent,focusKey=controlKey(focus);
 const scroll=animate?null:scrollSnapshot();
 root.replaceChildren();if(preview)root.append(notice('Демонстрационный режим. Подключения и реальные данные отключены.'));
 const fn={home,search,servers,settings}[page];if(fn)fn(root);else window.lvModules?.render(page,root);
 const parent=primaryRoute(page),related=pages.filter(([id])=>secondaryRoutes[id]===parent&&pageAllowed(id));
 if(related.length){const local=el('div','desktop-section-nav');local.setAttribute('role','group');local.setAttribute('aria-label','Инструменты раздела');for(const [id,,label] of [pages.find(p=>p[0]===parent),...related]){const tabLabel=parent==='search'?(id==='search'?'VPN · поиск и управление':'Информация по камере'):label;const b=btn(tabLabel,'quiet',()=>{if(page!==id)go(id)});b.dataset.sectionRoute=id;b.setAttribute('aria-pressed',String(page===id));local.append(b)}const heroNode=root.querySelector('.hero');if(heroNode)heroNode.after(local);else root.prepend(local);}
 if(!animate){
  const next=[...root.querySelectorAll('input,select,textarea,button')];let candidate=focusKey?next.find(n=>controlKey(n)===focusKey):null;if(!candidate&&idx>=0){const matching=next.filter(n=>n.tagName===tag&&n.type===type&&(n.closest('label')?.textContent||n.textContent)===caption);candidate=matching.length===1?matching[0]:next[idx];if(candidate&&!matching.includes(candidate))candidate=null}if(candidate&&!candidate.disabled)candidate.focus({preventScroll:true});
  restoreScroll(scroll);
 }else{
  window.scrollTo(0,0);
  if(motion&&!reduced.matches&&!document.hidden){const blocks=[...root.children].filter(n=>!n.classList.contains('notice')).slice(0,3);for(const [i,n] of blocks.entries())n.animate([{opacity:.65,transform:'translateY(7px)'},{opacity:1,transform:'translateY(0)'}],{duration:190,delay:i*22,easing:'cubic-bezier(.2,.75,.2,1)',fill:'backwards'})}
 }
}
function home(root){root.append(hero('РАБОЧАЯ ОБЛАСТЬ / 04','Рабочая панель','Управление инфраструктурой и обслуживание клиентов.'));const banner=el('div','home-banner');banner.append(add(el('div'),el('p','eyebrow',preview?'ДЕМОНСТРАЦИЯ':'РАБОЧАЯ СЕССИЯ'),el('h2','','Ваш центр управления'),el('p','',preview?'Разделы доступны для знакомства. Реальные запросы отключены.':'VPN, видеонаблюдение, биллинг и диагностика — в одном приложении.')),add(el('div','home-emblem','04'),el('small','','LINKVIDEO HELPER')));root.append(banner);const g=el('div','grid');for(const [id,icon,title,desc] of [['search','⌕','Поиск клиентов','По логину или NAT-порту'],['servers','▤','VPN-серверы','Состояние и ресурсы'],['settings','⚙','Настройки','Оформление и движение']]){if(!pageAllowed(id))continue;const b=btn('','tile',()=>go(id));add(b,add(el('span','symbol'),navIcon(id,icon)),el('strong','',title),el('p','',desc));g.append(b)}root.append(g);if(window.lvModules)window.lvModules.dashboard(root);root.append(notice(preview?'Демонстрационная сессия.':identity?.writes?(identity.destructive?'Тестовый режим записи и удаления разрешён внешней конфигурацией. Используйте только проверенный стенд.':'Тестовый режим записи разрешён внешней конфигурацией; удаление и восстановление запрещены.'):'Режим проверки: изменения корпоративных данных отключены по умолчанию.'))}
function table(headers,rows){const wrap=el('div','table-wrap'),t=el('table'),head=el('thead'),hr=el('tr'),body=el('tbody');wrap.tabIndex=0;wrap.setAttribute('role','region');wrap.setAttribute('aria-label','Таблица: '+headers.filter(Boolean).join(', '));for(const h of headers){const th=el('th','',h);th.scope='col';hr.append(th)}head.append(hr);for(const row of rows){const tr=el('tr');for(const c of row){const td=el('td');if(c instanceof Node)td.append(c);else td.textContent=String(c??'—');tr.append(td)}body.append(tr)}return add(wrap,add(t,head,body))}
function showMatches(root,state){root.replaceChildren();if(!state||(!state.loading&&!state.error&&!state.data))return;if(state.loading){root.append(busy('Проверяю VPN-серверы…'));return}if(state.error){root.append(notice(state.error));return}const r=state.data;if(state.draft!==undefined&&(state.draft.trim()!==state.query||(state.draftMode||state.mode)!==state.mode))root.append(notice('Показаны результаты предыдущего запроса: '+state.query+'. Нажмите «Найти» для обновления.'));root.append(notice('Проверено: '+r.checked+' / '+r.total+' · Найдено: '+r.matches.length+(r.errors.length?' · Не ответили или ошибка: '+r.errors.map(e=>e.server).join(', '):'')));if(!r.matches.length){root.append(notice(r.errors.length?'Совпадений в доступных ответах нет. Поиск неполный.':'Активных совпадений не найдено.'));if(r.archive_error)root.append(notice(r.archive_error));if(r.deleted?.length){root.append(el('h3','section-head','Архив PostgreSQL'));root.append(table(['Логин','Сервер','VPN IP','Удалён','Порты','Пароль',''],r.deleted.map(c=>{const action=btn('Восстановить','quiet',()=>window.lvModules.cloudRestore(c));action.disabled=identity?.role!=='sysadmin'||!c.password_saved;return [c.login,c.server,c.remote_address,c.deleted_at,c.ports.join(', '),c.password_saved?'Сохранён':'Нет',action]})))}return}root.append(table(['Логин','Сервер','VPN IP','Порты','Статус',''],r.matches.map(c=>[c.login,c.server,c.remote_address||'—',c.ports.join(', ')||'—',el('span',c.is_online?'online':'offline',c.is_online?'В сети':'Не в сети'),btn('Карточка','quiet',()=>window.lvModules.client(c.server,c.login))])))}
function search(root){
 root.append(hero('VPN / SEARCH','Поиск и управление','Найдите клиента, проверьте состояние VPN и управляйте его портами.'));
 const selected=window.lvModules?.selectedClient();
 if(selected){
  const back=btn('← К поиску','quiet',()=>{window.lvModules.clearClient();render(true);$('search-query')?.focus({preventScroll:true})});
  const compact=add(el('div','panel search-compact'),el('strong','','Клиент '+selected.login+' · карточка и управление'),back);root.append(compact);
  window.lvModules.renderClient(root);return;
 }
 const form=el('form','panel search-form-desktop'),input=el('input'),submit=el('button','primary','Найти'),result=el('div');result.id='search-results';input.id='search-query';
 let kind=searchState?.draftMode||searchState?.mode||'login';
 input.value=searchState?.draft??searchState?.query??'';input.maxLength=128;input.required=true;input.setAttribute('aria-label','Поисковый запрос');
 submit.type='submit';submit.disabled=preview||!!searchState?.loading;
 const refreshButton=btn('Обновить','quiet',()=>form.requestSubmit());refreshButton.disabled=submit.disabled||!input.value.trim();
 input.oninput=()=>{if(!searchState)searchState={};searchState.draft=input.value;refreshButton.disabled=submit.disabled||!input.value.trim()};
 const row=add(el('div','search-query-row'),input,submit,refreshButton),modes=el('div','desktop-section-nav search-modes');
 modes.setAttribute('role','group');modes.setAttribute('aria-label','Тип поиска');
 for(const [id,label] of [['login','По логину'],['port','По порту']]){const b=btn(label,'quiet',()=>{kind=id;if(!searchState)searchState={};searchState.draftMode=id;syncMode()});b.dataset.searchMode=id;b.disabled=!!searchState?.loading;modes.append(b)}
 function syncMode(){input.placeholder=kind==='port'?'Введите NAT-порт':'Введите логин клиента';for(const b of modes.children)b.setAttribute('aria-pressed',String(b.dataset.searchMode===kind))}
 syncMode();form.append(row,modes);root.append(form,result);showMatches(result,searchState);window.lvModules?.renderClient(root);
 form.onsubmit=async e=>{e.preventDefault();if(preview||taskBusy||searchState?.loading)return;const query=input.value.trim();if(!query)return;const owner=identity;window.lvModules.clearClient();searchState={query,mode:kind,loading:true};render(false);try{const data=await rpc('search',query,kind);if(identity===owner)searchState={query,mode:kind,data,draft:searchState?.draft??query,draftMode:searchState?.draftMode??kind}}catch(err){if(identity===owner)searchState={query,mode:kind,error:err.message,draft:searchState?.draft??query,draftMode:searchState?.draftMode??kind}}if(identity===owner&&page==='search')render(false)};
}

function servers(root){
 root.append(hero('VPN / INFRASTRUCTURE','VPN-серверы','Текущее состояние инфраструктуры.'));window.lvModules?.infrastructure(root);if(preview&&!serverState){root.append(notice('Для получения списка серверов войдите в рабочий аккаунт.'));return}
 const reload=btn('Обновить данные','primary',async()=>{const owner=identity;serverState={loading:true};render(false);try{const d=await rpc('operation','server_fleet',{});if(identity===owner)serverState={fleet:d,rows:d.rows}}catch(err){if(identity===owner)serverState={error:err.message}}if(identity===owner&&page==='servers')render(false)});reload.disabled=preview||!!serverState?.loading||!!serverState?.analyzing;root.append(add(el('div','actionbar'),reload));
 if(!serverState){root.append(notice('Нажмите «Обновить данные». Данные поступают из RouterOS, без подставных метрик.'));window.lvModules?.infrastructureResults(root);return}
 if(serverState.loading){root.append(busy('Параллельно проверяю VPN-серверы…'));return}
 if(serverState.error)root.append(notice(serverState.error));if(!serverState.rows){window.lvModules?.infrastructureResults(root);return}
 const rows=serverState.rows,ok=rows.filter(x=>x.metrics),sum=k=>ok.reduce((a,x)=>a+(x.metrics[k]||0),0);
 const metrics=el('div','grid');for(const [label,value] of [['Ответили',ok.length+' / '+rows.length],['VPN онлайн',sum('clients_online').toLocaleString('ru-RU')],['Порты онлайн / всего',sum('ports_active').toLocaleString('ru-RU')+' / '+sum('ports_total').toLocaleString('ru-RU')]])metrics.append(add(el('div','tile'),el('small','',label),el('div','metric',value)));root.append(metrics);
 if(serverState.fleet?.cancelled)root.append(notice('Проверка остановлена. Показатели отражают только полученные ответы.'));else if(ok.length!==rows.length)root.append(notice('Часть серверов не вернула подтверждённые данные.'));
 function meter(value){if(value==null)return el('span','muted','—');const p=el('progress','fleet-meter');p.max=100;p.value=Math.max(0,Math.min(100,value));const label=el('span','',value+'%');return add(el('div','fleet-load'),p,label)}
 root.append(table(['VPN-сервер','Страна','Активные L2TP','Учётки','CPU','RAM','Порты онлайн / всего','LV-автоматика',''],rows.map(row=>{const x=row.metrics;const b=btn('Подробнее','quiet',async()=>{const owner=identity,previous=serverState;serverState={...previous,analyzing:row.host};render(false);try{const metric=await rpc('analyze',row.host);if(identity===owner)serverState={...previous,metric}}catch(err){if(identity===owner)serverState={...previous,error:err.message}}if(identity===owner&&page==='servers')render(false)});b.disabled=preview||!!serverState.analyzing;return [row.host,row.country,x?x.clients_online:'Нет данных',x?x.clients_total:'—',x?meter(x.cpu_load):'—',x?meter(x.memory_usage_percent):'—',x?x.ports_active+' / '+x.ports_total:'—',window.lvModules?.lvLabel(row.host)||'Не проверено',b]})));
 if(serverState.analyzing)root.append(busy('Проверяю '+serverState.analyzing+'…'));const m=serverState.metric;if(m){root.append(el('h3','section-head',m.server));const grid=el('div','grid');for(const [label,value] of [['CPU',m.cpu_load==null?'—':m.cpu_load+'%'],['Память',m.memory_usage_percent==null?'—':m.memory_usage_percent+'%'],['VPN онлайн',m.clients_online+' / '+m.clients_total],['NAT-порты',m.ports_total],['Активные порты',m.ports_active]])grid.append(add(el('div','tile'),el('small','',label),el('div','metric',value)));root.append(grid);window.lvModules?.infrastructure(root,m)}window.lvModules?.infrastructureResults(root);
}

let updateState=null;
function updatePanel(root){const card=el('div','panel update-panel');card.append(add(el('div','update-heading'),add(el('div'),el('p','eyebrow','PREVIEW CHANNEL'),el('h3','','Обновления 4.0')),el('span','version','4.0.0-alpha.13')));card.append(el('p','','Проверка отдельного канала GitHub. Старые версии 3.x исключены. Установщик проверяется по SHA-256. Можно сохранить EXE или запустить обычную установку из приложения после подтверждения.'));const check=btn('Проверить обновления','primary',async()=>{check.disabled=true;try{updateState=await rpc('updateCheck');if(page==='settings')render(false)}catch(e){toast('Обновления: '+e.message)}finally{check.disabled=false}});card.append(add(el('div','actionbar'),check));if(updateState){const s=updateState;if(!s.available)card.append(notice('В доступных релизах канала нет более новой версии с проверяемым установщиком.'));else{card.append(el('h3','','Доступна '+s.release.version),el('small','',Math.round(s.release.size/1024/1024)+' МБ'),el('p','release-notes',s.release.notes||'Описание не опубликовано.'));const b=btn('Скачать проверенный EXE','primary',async()=>{b.disabled=true;try{const out=await rpc('updateDownload');toast('SHA-256 подтверждён. Файл сохранён: '+out.path)}catch(e){toast('Обновления: '+e.message)}finally{b.disabled=false}});const install=btn('Скачать и установить','primary',async()=>{install.disabled=true;try{const result=await rpc('updateInstall');if(result.launched)toast('Установщик запущен')}catch(e){toast('Обновления: '+e.message)}finally{install.disabled=false}});card.append(add(el('div','actionbar'),b,install))}}root.append(card)}
let settingsTab='appearance';
function settings(root){
 root.append(hero('HELPER / PREFERENCES','Настройки','Персонализация и подключение сервисов.'));
 const tabs=el('div','desktop-section-nav settings-tabs');
 for(const [id,label] of [['appearance','Внешний вид'],['cloud','Облачный сервер'],['archive','Архив'],['servers','VPN-серверы'],['about','О программе']]){
  if(id==='servers'&&!preview&&identity?.role!=='sysadmin')continue;
  const b=btn(label,'quiet',()=>{if(settingsTab===id)return;window.lvModules?.discardPlan();settingsTab=id;render(true)});b.dataset.settingsTab=id;b.setAttribute('aria-pressed',String(settingsTab===id));tabs.append(b)
 }
 root.append(tabs);
 if(settingsTab==='appearance'){
  const layout=el('div','create-workspace settings-appearance'),card=el('div','panel create-form'),sample=el('div','panel create-result');
  card.append(el('h2','','Внешний вид'),el('p','muted','Выберите тему оформления интерфейса.'));
  const grid=el('div','settings-theme-grid');for(const [id,name] of themes){const b=btn('','theme-choice',()=>setTheme(id));const sample=el('span','theme-swatch');sample.setAttribute('aria-hidden','true');sample.dataset.swatch=id;sample.append(el('i'),el('i'),el('i'));b.append(sample,el('span','theme-choice-label',name));b.dataset.themeChoice=id;b.setAttribute('aria-pressed',String(theme===id));grid.append(b)}card.append(grid);
  const sw=btn('','switch',()=>{motion=!motion;save('lv4-motion',motion?'on':'off');preferences();render(false)});sw.setAttribute('role','switch');sw.setAttribute('aria-label','Плавные анимации интерфейса');sw.setAttribute('aria-checked',String(motion));sw.append(el('span'));card.append(add(el('div','setting-row'),el('h3','','Плавные анимации интерфейса'),sw));
  sample.append(el('h2','','Предпросмотр'),add(el('div','settings-demo'),el('strong','','LinkVideo.Helper'),el('span','settings-demo-active','Активный раздел'),el('span','muted','Другой раздел'),el('span','settings-demo-action','Основная кнопка')));layout.append(card,sample);root.append(layout,notice('Изолированный профиль 4.0. Настройки 3.x не изменяются.'));return
 }
 if(settingsTab==='about'){root.append(add(el('div','panel'),el('h2','','LinkVideo.Helper'),el('p','muted','Версия 4.0 · предварительный канал Electron')));updatePanel(root)}
 window.lvModules?.settings(root,settingsTab)
}

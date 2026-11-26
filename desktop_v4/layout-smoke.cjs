'use strict';
// Data is entirely synthetic. No credentials, no network, no worker mutation.
async function layoutSmoke(){
 if(!window.helper.smokeMode)throw Error('Layout smoke disabled');
 const wait=()=>new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)));
 const long='Подтверждённая информация о клиенте · '.repeat(8),login='test_'+ 'long_account_'.repeat(7),host='fixture-vpn.example.invalid',ports=[10000,10001,10002];
 const c={server:host,login,remote_address:'172.16.0.1',ports,active_ports:[10000],disabled_ports:[10001],is_online:true,is_enabled:true,uptime:'2д 14ч',last_logged_out:'2026-11-15 14:15:00',rx_bytes:1048576,tx_bytes:999999,port_nat_bytes:{10000:500},port_nat_packets:{10000:30},conflicts_verified:true,port_conflicts:{}};
 const d={camera:long,server:host,timezone:7,requested_start:1700000000,requested_end:1700003600,requested_seconds:3600,covered_seconds:3300,coverage:91.7,downloadable:true,exact_timeline:true,checked:2,error_count:0,gaps:[{start:1700000100,end:1700000400}],slices:[{start:1700000400,end:1700003600,host}],moves:[{from:host,to:'reserve.example.invalid',start:1700000100,end:1700000400,overlap:true}],checked_hosts:[host],diagnosis:{title:'Диагностическое заключение',side:'Не подтверждено',confidence:'Ограниченная уверенность',summary:long,cautions:[long],evidence:[{title:'Факт',detail:long}],gaps:[]},reports:{summary:long,breaks:long,moves:long,compare:long,technical:long},server_compare:[],address_compare:[]};
 const customer={id:'c1',login,display_name:long,country:'Казахстан',status:'Активный',balance_text:'1000 KZT',services_loaded:true,services:[{id:'001',camera_id:'001',camera_vc_id:'10001',camera_name:long,tariff_name:long,status:'Активный',price_text:'1000',discount_text:'10%',total_text:'900'}],address:long,email:login+'@example.invalid'};
 const data={hosts:[{host,country:'Россия'}],host,client:c,newPorts:[10000],admin:{username:login,crm:true,b2o:true},inactive:{rows:[{...c,is_online:false,lifecycle_state:'Q'}],errors:[],total:1,cancelled:false},inactiveSelected:[],lvFleet:{rows:[{server:host,state_text:long,version:'4.0',initialized:3,aging_enabled:true,installed:true}],cancelled:false},backup:{success:1,failed:0,folder:'C:/'+('long-directory/'.repeat(10)),servers:[{server:host,ok:true}]},backupInventory:[{id:'FULL_fixture',created:'2026-11-17',success:1,failed:0,files:6}],billingClient:customer,catalog:{scope:'all',tariffs:[{id:'1',name:long,country:'Казахстан',account_type:'ФЛ',archive_days:30,bitrate_mbps:4,price:'1200',currency:'KZT',test_days:7,description:long}],countries:[{name:'Казахстан'}],account_types:['ФЛ'],total:1},archive:d,cameraInfo:{camera_id:'10001',name:long,server:host,ip:'192.0.2.1',rtsp_port:554,ping_text:'12 мс',tcp_text:'Доступен',status:'В сети',serial:'123456789',model:long},cameraChoices:[{camera_id:'10001',label:long,country:'Россия',status:'В сети',operator_id:241}],agent:{serial_number:'123456789',server:host,model:long,vendor:'Тест',version:'1.0',connect_time:'2026-11-17'},agentAction:'bitrate',agentSettings:{bitrate:2048},templates:{categories:['Диагностика'],items:[{id:1,title:long,body:long+' https://example.invalid/'+('abcdef'.repeat(90)),category:'Диагностика',favorite:true}]},editTemplate:{title:long,category:'Диагностика',body:long}};
 const checks=[];
 function check(name){
  const width=document.documentElement.clientWidth,scroll=document.documentElement.scrollWidth;
  if(scroll>width+3)throw Error('Horizontal document overflow: '+name+' '+scroll+'/'+width);
  for(const n of document.querySelectorAll('#page button,#page label,#page h1,#page h2,#page h3,#page .kv strong,#page .notice,#nav button,#page input,#page select,#page textarea')){
   if(!n.getClientRects().length)continue;
   if(n.closest('.table-wrap'))continue;
   const box=n.getBoundingClientRect();if(box.left< -3||box.right>width+3)throw Error('Control escaped viewport: '+name+' '+n.tagName+' '+n.textContent.slice(0,60));
   if(!['INPUT','SELECT','TEXTAREA'].includes(n.tagName)&&n.clientWidth>0&&n.scrollWidth>n.clientWidth+4)throw Error('Clipped text: '+name+' '+n.textContent.slice(0,90));
  }
  for(const t of document.querySelectorAll('.table-wrap')){const b=t.getBoundingClientRect();if(b.right>width+3||b.left< -3)throw Error('Table escaped viewport: '+name)}
  checks.push(name);
 }
 if(!identity)enter({username:login,role:'sysadmin',writes:false},true);
 else{enter({username:login,role:'sysadmin',writes:false},true)}
 setTheme('dark');motion=true;preferences();
 for(const name of ['home','search','servers','create','inactive','lv','recovery','billing','archives','diagnostics','agent','camera','templates','admin','settings']){
  go(name);window.lvModules.fixture(name,data);await wait();check(name);
 }
 go('camera');window.lvModules.fixture('camera',data);await wait();if(!document.querySelector('.camera-detail-workspace'))throw Error('Missing camera detail workspace');document.querySelector('.search-compact button').click();await wait();if(!document.querySelector('[data-camera-choice="10001"]'))throw Error('Cannot return to camera results');check('camera:results');
 go('servers');serverState={rows:[{host,country:'Россия',metrics:{server:host,clients_online:3,clients_total:8,ports_active:5,ports_total:12,cpu_load:27,memory_usage_percent:45}}],metric:{server:host,clients_online:3,clients_total:8,ports_active:5,ports_total:12,cpu_load:27,memory_usage_percent:45}};render(false);await wait();if(!document.querySelector('#page').textContent.includes('Порты онлайн / всего')||!document.querySelector('#page').textContent.includes('Выгрузить этот сервер'))throw Error('Infrastructure workspace missing');check('servers:details');serverState=null;
 go('templates');window.lvModules.fixture('templates',{...data,editTemplate:null});await wait();const item=document.querySelector('[data-template-id="1"]');if(!item)throw Error('Missing template library');item.click();await wait();if(!document.querySelector('.template-answer'))throw Error('Missing selected template');check('templates:selection');
 go('archives');window.lvModules.fixture('archives',data);await wait();
 if(!document.querySelector('.archive-workspace')||!document.querySelector('[data-archive-folder]')||[...document.querySelectorAll('input[type=datetime-local]')].some(x=>!x.value))throw Error('Archive workflow missing');
 go('diagnostics');window.lvModules.fixture('diagnostics',data);await wait();const extra=document.querySelector('.archive-extra-rows');extra.querySelector('button').click();if(!extra.querySelector('input'))throw Error('Cannot add camera comparison');extra.querySelector('input').value='fixture';extra.querySelector('input').dispatchEvent(new Event('input'));render(false);if(document.querySelector('.archive-extra-rows input').value!=='fixture')throw Error('Comparison input lost');check('diagnostics:extra');
 go('agent');window.lvModules.fixture('agent',data);await wait();if(!document.querySelector('.agent-workspace')||!document.querySelector('[data-agent-action="bitrate"]'))throw Error('Agent workflow missing');document.querySelector('[data-agent-action="timezone"]').click();await wait();if(!document.querySelector('#page').textContent.includes('ntp.goodline.info'))throw Error('Agent NTP setting missing');check('agent:timezone');
 go('billing');window.lvModules.fixture('billing',{...data,billingTab:'search',billingCardTab:'services',catalog:{...data.catalog,scope:'client'}});await wait();
 const billingTab=(kind,id)=>document.querySelector('[data-billing-tab="'+kind+':'+id+'"]');
 if(!document.querySelector('[data-billing-camera="001"]'))throw Error('Missing native camera card');
 billingTab('client','info').click();await wait();if(document.querySelector('.billing-camera-list')||!document.querySelector('#page').textContent.includes('Дата регистрации'))throw Error('Billing info tab failed');
 billingTab('client','services').click();await wait();if(!document.querySelector('.billing-camera-list'))throw Error('Billing services tab failed');check('billing:services');
 billingTab('workspace','create').click();await wait();if(!document.querySelector('.billing-create-fields')||document.querySelector('.billing-camera-list'))throw Error('B2O creation tab failed');check('billing:create');
 billingTab('workspace','all').click();window.lvModules.fixture('billing',{catalog:data.catalog});await wait();if(!document.querySelector('#page').textContent.includes('Показано 1 из 1')||document.querySelector('.billing-create-fields'))throw Error('All tariffs tab failed');check('billing:all');
 billingTab('workspace','search').click();await wait();if(!document.querySelector('.billing-camera-list'))throw Error('Billing search state lost');
 go('search');const many=Array.from({length:48},(_,i)=>20000+i);const crowded={...c,ports:many,active_ports:many,disabled_ports:[]};window.lvModules.fixture('search',{client:crowded,newPorts:[],clientHistory:Array.from({length:25},()=>({created_at:'2026-11-17',action:'client.create',details:{}}))});await wait();
 const pane=key=>document.querySelector('[data-scroll-key="'+key+'"]');
 const info=pane('vpn-info'),actions=pane('vpn-actions');if(!info||!actions||info.scrollHeight<=info.clientHeight||actions.scrollHeight<=actions.clientHeight)throw Error('Expected independent client scroll panes');
 info.scrollTop=250;actions.scrollTop=280;window.scrollTo(0,Math.min(150,document.scrollingElement.scrollHeight-innerHeight));await wait();
 const positions=()=>[window.scrollY,pane('vpn-info').scrollTop,pane('vpn-actions').scrollTop];
 const saved=positions();if(saved[1]<=0||saved[2]<=0)throw Error('Scroll test did not scroll both panes');
 const portButton=document.querySelector('[data-port-select="20020"]');if(!portButton)throw Error('Missing native NAT port card');portButton.click();if(document.getElementById('vpn-selected-port').textContent!=='Выбран порт 20020')throw Error('Selected port not linked to controls');
 const actionInput=pane('vpn-actions').querySelector('input');actionInput.value='unsaved input';actionInput.focus({preventScroll:true});
 window.lvModules.refreshFixture({...crowded,rx_bytes:99999999,uptime:'3д',is_online:false});await wait();
 if(positions().some((v,i)=>Math.abs(v-saved[i])>2)||document.activeElement!==actionInput||actionInput.value!=='unsaved input')throw Error('Live VPN refresh moved viewport or lost input: '+positions()+' / '+saved);
 if(document.getElementById('client-online').textContent!=='Не в сети')throw Error('Live status did not change');if(document.querySelector('[data-port-select="20020"]').getAttribute('aria-pressed')!=='true')throw Error('Selected port lost during refresh');
 render(false);await wait();if(positions().some((v,i)=>Math.abs(v-saved[i])>2))throw Error('Full render lost client scroll: '+positions()+' / '+saved);
 const beforeTask=positions();taskUI(true,'Synthetic progress');await wait();if(positions().some((v,i)=>Math.abs(v-beforeTask[i])>2))throw Error('Progress overlay shifted scroll');taskUI(false);await wait();if(positions().some((v,i)=>Math.abs(v-beforeTask[i])>2))throw Error('Closing progress shifted scroll');
 check('search:scroll-preservation');
 document.querySelector('.search-compact button').click();await wait();if(!document.getElementById('search-query'))throw Error('Cannot return to search');const draft=$('search-query');draft.value='unsent_fixture';draft.dispatchEvent(new Event('input'));document.querySelector('[data-search-mode="port"]').click();draft.focus({preventScroll:true});draft.setSelectionRange(2,7);render(false);if($('search-query').selectionStart!==2||$('search-query').selectionEnd!==7||$('search-query').value!=='unsent_fixture'||document.activeElement!==$('search-query')||document.querySelector('[data-search-mode="port"]').getAttribute('aria-pressed')!=='true')throw Error('Search draft/focus lost');check('search:draft');
 go('create');window.lvModules.fixture('create',{hosts:[{host:'ru.example.invalid',country:'Россия'},{host:'kz.example.invalid',country:'Казахстан'}],createRegion:'Россия',createHost:'',createPorts:3,createCount:1});await wait();
 const control=id=>document.querySelector('[data-create-control="'+id+'"]');
 if(control('ports').value!=='3'||control('count').value!=='1'||control('host').value!=='ru.example.invalid'||!document.querySelector('.create-result'))throw Error('Desktop create defaults differ');
 const country=value=>{control('region').value=value;control('region').dispatchEvent(new Event('change'))};
 country('Казахстан');if(control('host').value!=='kz.example.invalid')throw Error('Country filter failed');
 country('Беларусь');if(control('host').value!==''||!document.querySelector('[data-create-action="manual"]').disabled)throw Error('Unavailable country fell back to another region');
 country('Россия');check('create:region');window.lvModules.expireFixture();await new Promise(r=>setTimeout(r,140));const expired=document.querySelector('.confirmation-expired');if(!expired||!expired.querySelector('[data-plan-confirm]').disabled||!expired.textContent.includes('Срок действия истёк'))throw Error('Expired confirmation remained active');expired.querySelector('[data-plan-cancel]').click();await wait();if(document.querySelector('.confirmation'))throw Error('Expired confirmation not dismissed');check('create:confirmation-expiry');
 go('settings');for(const id of ['cloud','archive','servers','about','appearance']){const b=document.querySelector('[data-settings-tab="'+id+'"]');if(!b)throw Error('Missing native settings tab '+id);b.click();await wait();check('settings:'+id)}if(document.querySelectorAll('[data-theme-choice]').length!==themes.length)throw Error('Missing theme choices');
 go('settings');for(const [id] of themes){setTheme(id);await wait();check('theme:'+id)}
 setTheme('dark');motion=false;preferences();go('home');await wait();if($('page').getAnimations({subtree:true}).length)throw Error('Animation active with reduced motion');
 motion=true;preferences();go('billing');window.lvModules.fixture('billing',data);await wait();check('billing:screenshot');
 window.__lv4CaptureFixture=async name=>{motion=false;preferences();go(name);window.lvModules.fixture(name,{...data,editTemplate:null,billingTab:'search',billingCardTab:'services'});if(name==='servers'){serverState={rows:[{host,country:'Россия',metrics:{server:host,clients_online:3,clients_total:8,ports_active:5,ports_total:12,cpu_load:27,memory_usage_percent:45}}],metric:{server:host,clients_online:3,clients_total:8,ports_active:5,ports_total:12,cpu_load:27,memory_usage_percent:45}};render(false)}if(name==='templates')document.querySelector('[data-template-id="1"]')?.click();await wait();};
 return {checks,viewport:[innerWidth,innerHeight]};
}
module.exports={layoutSmoke};

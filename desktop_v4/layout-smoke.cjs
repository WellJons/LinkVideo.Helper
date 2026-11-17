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
  for(const n of document.querySelectorAll('#page button,#page label,#page h1,#page h2,#page h3,#page .kv strong,#page .notice,#nav button')){
   if(!n.getClientRects().length)continue;
   if(n.closest('.table-wrap'))continue;
   if(n.clientWidth>0&&n.scrollWidth>n.clientWidth+4)throw Error('Clipped text: '+name+' '+n.textContent.slice(0,90));
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
 go('settings');for(const [id] of themes){setTheme(id);await wait();check('theme:'+id)}
 setTheme('dark');motion=false;preferences();go('home');await wait();if($('page').getAnimations().length)throw Error('Animation active with reduced motion');
 motion=true;preferences();go('billing');window.lvModules.fixture('billing',data);await wait();check('billing:screenshot');
 return {checks,viewport:[innerWidth,innerHeight]};
}
module.exports={layoutSmoke};

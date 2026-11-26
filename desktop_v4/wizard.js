'use strict';
const $=id=>document.getElementById(id);
let info,page='welcome',option=true,launch=true,failure='';
const el=(tag,cls,text)=>{const n=document.createElement(tag);if(cls)n.className=cls;if(text!=null)n.textContent=text;return n};
function check(label,value,cb){const l=el('label','check'),i=el('input');i.type='checkbox';i.checked=value;i.onchange=()=>cb(i.checked);l.append(i,el('span','',label));return l}
function welcome(body){
 const mark=el('div','accent');body.append(mark);
 const cards=el('div','cards');
 for(const [title,desc] of [['VPN-клиенты','Создание, поиск и управление L2TP-клиентами.'],['Архив','Поиск, выгрузка и диагностика записей камер.'],['Инфраструктура','VPN-серверы, резервные копии и LV Automation.']]){const card=el('article','card');card.append(el('div','tick','✓'),el('b','',title),el('p','',desc));cards.append(card)}
 const req=el('div','requirements'),row=el('div');req.append(el('b','','Системные требования'),row);for(const s of ['Windows 10/11 · x64','Сеть до VPN-серверов','Без администратора'])row.append(el('span','',s));body.append(cards,req)
}
function render(){
 const install=info.mode==='install',body=$('body');body.replaceChildren();
 const steps=install?[['welcome','Приветствие'],['options','Параметры'],['progress','Установка'],['finish','Готово']]:[['welcome','Подтверждение'],['progress','Удаление'],['finish','Готово']];
 const current=steps.findIndex(x=>x[0]===(page==='error'?'finish':page));$('steps').replaceChildren();steps.forEach(([id,label],i)=>{const n=el('li',i===current?'active':i<current?'done':'',label);if(i===current)n.setAttribute('aria-current','step');$('steps').append(n)});
 $('back').hidden=page!=='options';$('cancel').hidden=!['welcome','options'].includes(page);$('next').hidden=page==='progress';
 $('next').textContent=page==='finish'||page==='error'?'Готово':page==='options'?'Установить':install?'Далее':'Удалить';
 let title='',desc='';
 if(page==='welcome'){
  if(install){title=info.upgrade?'Обновление LinkVideo.Helper 4':'LinkVideo.Helper 4';desc='Единый рабочий инструмент для VPN, архивов и диагностики LinkVideo.';welcome(body)}
  else{title='Удаление LinkVideo.Helper 4';desc='Подтвердите удаление программы с этого компьютера.';const form=el('div','form');form.append(el('p','','LinkVideo.Helper 4 и ярлыки будут удалены.'),check('Удалить также настройки и кэш версии 4.0',option,v=>option=v),el('p','hint','По умолчанию настройки сохраняются. Версия 3.x и файлы вне профиля 4.0 не затрагиваются.'));body.append(form)}
 }else if(page==='options'){
  title='Параметры установки';desc='Проверьте расположение программы и создание ярлыка.';const form=el('div','form'),field=el('div','field');field.append(el('span','','Папка установки'),el('div','path',info.target));form.append(field,check('Создать ярлык на рабочем столе',option,v=>option=v),el('p','hint','Версия 4.0 устанавливается отдельно от 3.x. Настройки и данные входа при обновлении сохраняются.'));body.append(form)
 }else if(page==='progress'){
  title=install?(info.upgrade?'Обновление LinkVideo.Helper 4':'Установка LinkVideo.Helper 4'):'Удаление LinkVideo.Helper 4';desc='Не закрывайте окно до завершения процесса.';const progress=el('div','progress');progress.setAttribute('role','progressbar');progress.setAttribute('aria-valuemin','0');progress.setAttribute('aria-valuemax','100');progress.append(el('span'));body.append(progress,el('div','progress-label','Подготовка…'))
 }else if(page==='finish'){
  title=install?'Готово':'Удаление завершено';desc=install?'LinkVideo.Helper '+info.version+' успешно установлен.':'LinkVideo.Helper 4 удалён с компьютера.';const form=el('div','form');form.append(el('p','',install?'Программа зарегистрирована в Windows. Удалить её можно через «Установленные приложения».':'Окно можно закрыть.'));if(install)form.append(check('Запустить LinkVideo.Helper 4',launch,v=>launch=v));body.append(form)
 }else{
  title=install?'Установка не завершена':'Удаление завершено с ошибкой';desc='Не удалось завершить операцию.';body.append(el('div','error',failure))
 }
 $('title').textContent=title;$('description').textContent=desc;$('content').scrollTop=0;
}
function progress(p){const bar=document.querySelector('.progress');if(!bar)return;bar.setAttribute('aria-valuenow',String(p.percent));bar.firstChild.style.width=p.percent+'%';document.querySelector('.progress-label').textContent=p.percent+'% · '+p.label}
async function next(){if(info.preview)return;if(page==='welcome'&&info.mode==='install'){page='options';render();return}if(page==='welcome'||page==='options'){page='progress';render();try{const r=await window.wizard.run(option);page=r.ok?'finish':'error';failure=r.error||''}catch(e){page='error';failure=e.message}render();return}if(page==='finish'||page==='error'){const r=await window.wizard.close(page==='finish'&&info.mode==='install'&&launch);if(!r.ok){failure=r.error;page='error';render()}}}
(async()=>{
 const r=await window.wizard.info();if(!r.ok)throw Error(r.error);info=r.data;option=info.mode==='install';$('version').textContent='Версия '+info.version;render();
 $('next').onclick=next;$('back').onclick=()=>{page='welcome';render()};$('cancel').onclick=()=>window.wizard.close(false);window.wizard.progress(progress);
 if(info.preview)window.wizardFixture=p=>{page=p;failure='Не удалось заменить файл. Закройте приложение и повторите операцию.';render();if(p==='progress')progress({percent:62,label:'Копирование файлов…'});const content=$('content'),footer=document.querySelector('footer');return document.documentElement.scrollWidth<=innerWidth+1&&footer.scrollWidth<=footer.clientWidth+1&&content.scrollWidth<=content.clientWidth+1&&[...footer.querySelectorAll('button:not([hidden])')].every(b=>b.getBoundingClientRect().right<=innerWidth+1&&b.getBoundingClientRect().bottom<=innerHeight+1)};
})().catch(e=>{$('title').textContent='Ошибка запуска мастера';$('description').textContent=e.message;$('next').hidden=true;$('back').hidden=true;$('cancel').onclick=()=>window.wizard.close(false)});

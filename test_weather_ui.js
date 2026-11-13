"use strict";
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(require('node:path').join(__dirname, 'static', 'app.js'), 'utf8');
class Element {
  constructor(id='') { this.id=id; this.value=''; this.textContent=''; this.hidden=false; this.disabled=false; this.placeholder=''; this.listeners={}; this.dataset={}; this.classList={toggle(){},remove(){},add(){}}; }
  addEventListener(type, fn) { this.listeners[type]=fn; }
  dispatch(type) { return this.listeners[type]?.({preventDefault(){}}); }
  replaceChildren(...items) { this.items=items; }
  add(item) { (this.items ||= []).push(item); }
}
async function run(missing='') {
  const ids=['yield-form','toast','state','district','season','crop','refresh-weather','weather-warning','weather-loading',
    'temperature','humidity','rainfall','temperature-source','humidity-source','rainfall-source',
    'temperature-spinner','humidity-spinner','rainfall-spinner','temperature-warning','humidity-warning','rainfall-warning'];
  const els=new Map(ids.filter(id=>id!==missing).map(id=>[id,new Element(id)]));
  const resets=['temperature','humidity','rainfall'].map(field=>{const e=new Element();e.dataset.field=field;return e;});
  const form=els.get('yield-form'); form.elements={namedItem:()=>null};
  const document={getElementById:id=>els.get(id)||null,querySelector:s=>s.startsWith('.reset-weather[data-field=')?resets.find(x=>s.includes(`"${x.dataset.field}"`))||null:null,
    querySelectorAll:s=>s==='.reset-weather'?resets:[],createElement:()=>new Element(),addEventListener(){}};
  const weather={values:{temperature:22.9,humidity:65,rainfall:970},years:[2021,2022,2023,2024,2025],rainfall_by_year:{2021:910,2022:960,2023:1000,2024:1020,2025:960},source:'Open-Meteo Archive',fetched_at:'2026-11-13 12:00 IST'};
  const context={document,console:{warn(){},error:console.error},Option:class{constructor(text,value){this.text=text;this.value=value;}},URLSearchParams,AbortController,
    setTimeout,clearTimeout,localStorage:{getItem(){return null},setItem(){}},sessionStorage:{getItem(){return null},setItem(){}},
    window:{GREENYIELD_MESSAGES:{},GreenYieldI18n:{translate:x=>x,apply(){},lang:()=> 'en'},GreenYieldLocal:{reachable:async()=>true}},
    fetch:async url=>url==='/api/meta'?{ok:true,json:async()=>({districts_by_state:{'Uttar Pradesh':['Saharanpur']},crops_by_season:{Rabi:['Wheat']},seasons:['Rabi'],typical_soil_by_region:{}})}:{ok:true,json:async()=>weather}};
  context.window.window=context.window;
  vm.runInNewContext(source,context,{filename:'static/app.js'});
  await new Promise(resolve=>setTimeout(resolve,0));
  els.get('state').value='Uttar Pradesh'; els.get('state').dispatch('change');
  els.get('district').value='Saharanpur'; els.get('district').dispatch('change');
  await new Promise(resolve=>setTimeout(resolve,20));
  for(const name of ['humidity','rainfall'])assert.equal(els.get(name).value,weather.values[name]);
  if(!missing){assert.equal(els.get('temperature').value,weather.values.temperature);assert.match(els.get('temperature-source').textContent,/Auto-filled from Open-Meteo Archive/);}
}
(async()=>{await run();await run('temperature');console.log('PASS: weather fills all fields; missing temperature element does not block humidity/rainfall.');})().catch(error=>{console.error(error);process.exitCode=1;});

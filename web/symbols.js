/* Pure state helpers, shared by the UI and deterministic tests. */
(function(root,factory){if(typeof module==='object'&&module.exports)module.exports=factory();else root.ChartTools=factory();})(globalThis,function(){
'use strict';
function inputSymbol(value){const s=String(value||'').trim().toUpperCase();if(!/^[A-Z0-9^][A-Z0-9.^_=!:\-]{0,39}$/.test(s)||s.includes('..')||(s.match(/:/g)||[]).length>1||s.endsWith(':'))throw Error('Enter one symbol, for example NVDA, SPX, ES, BRK.B or NASDAQ:AMD. URLs and formula expressions are not accepted.');return s;}
function nextDelay(base,failures){if(![15,60,300].includes(Number(base)))return 0;return Math.min(900,Number(base)*2**Math.min(Math.max(0,failures),6));}
function validResult(request,current,snapshot){return request.generation===current.generation&&request.symbol===current.symbol&&request.provider===current.provider&&request.interval===current.interval&&snapshot.symbol===current.symbol;}
function quality(s,now=Date.now()){
 if(!s)return {label:'NO LOCAL DATA',age:null,detail:'An online chart is display-only and is not an input to this engine.'};
 const stamp=Date.parse(s.spotAsOf),age=Number.isFinite(stamp)?Math.max(0,Math.floor((now-stamp)/1000)):null,kind=s.dataKind;
 if(kind==='synthetic'||kind==='sandbox')return {label:kind==='synthetic'?'SYNTHETIC':'ARCHIVED SANDBOX',age,detail:'Not current market data.'};
 if(age===null||stamp>now+5000)return {label:'SOURCE TIME UNVERIFIED',age,detail:'The source timestamp is invalid or ahead of this computer clock.'};
 if(kind==='imported')return {label:'IMPORTED · SOURCE UNVERIFIED',age,detail:'Saved/imported provenance is user supplied. No real-time transport status is inferred.'};
 if(s.feed==='alpaca-iex')return {label:age>90?'IEX · OLD LAST TRADE':'IEX · REAL-TIME SOURCE / POLLED',age,detail:'Single exchange, not SIP/NBBO. Check the source trade time; a refresh is not a new trade.'};
 return {label:s.feed==='yahoo'?'YAHOO · DELAY UNKNOWN':'IMPORTED · UNVERIFIED',age,detail:'Auto-refresh cannot remove provider delay. No execution-grade status is inferred.'};
}
function isSPX(s){return Boolean(s&&s.symbol==='SPX'&&Array.isArray(s.options)&&s.options.length);}
function ageText(seconds){if(seconds==null||!Number.isFinite(seconds))return 'unknown';if(seconds<60)return seconds+'s';if(seconds<3600)return Math.floor(seconds/60)+'m '+seconds%60+'s';if(seconds<86400)return Math.floor(seconds/3600)+'h '+Math.floor(seconds%3600/60)+'m';return Math.floor(seconds/86400)+'d '+Math.floor(seconds%86400/3600)+'h';}
return {inputSymbol,nextDelay,validResult,quality,isSPX,ageText};
});

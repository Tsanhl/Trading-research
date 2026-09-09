/* Runs in an opaque sandbox, never in the private app's origin. No data bridge. */
'use strict';
(()=>{
 const q=new URLSearchParams(location.search),symbol=q.get('symbol')||'NASDAQ:AAPL',interval=q.get('interval')||'15',timezone=q.get('timezone')||'Europe/London';
 if(!/^[A-Z0-9^][A-Z0-9.^_=!:\-]{0,39}$/.test(symbol)||symbol.includes('..')||!['1','5','15','60','240','D','W'].includes(interval)||!['Europe/London','America/New_York','Etc/UTC'].includes(timezone)){document.getElementById('status').textContent='Invalid chart request';return;}
 const container=document.createElement('div');container.className='tradingview-widget-container';
 const inner=document.createElement('div');inner.className='tradingview-widget-container__widget';container.appendChild(inner);
 const attribution=document.createElement('div');attribution.className='tradingview-widget-copyright';
 const link=document.createElement('a');link.href='https://www.tradingview.com/chart/?symbol='+encodeURIComponent(symbol);link.target='_blank';link.rel='noopener noreferrer';link.textContent=symbol+' chart by TradingView';attribution.appendChild(link);container.appendChild(attribution);
 const script=document.createElement('script');script.src='https://s3.tradingview.com/external-embedding/embed-widget-advanced-chart.js';script.type='text/javascript';script.async=true;
 script.textContent=JSON.stringify({autosize:true,symbol,interval,timezone,theme:'dark',style:'1',locale:'en',allow_symbol_change:true,hide_top_toolbar:false,hide_side_toolbar:false,withdateranges:true,details:false,calendar:false,support_host:'https://www.tradingview.com'});
 script.onerror=()=>{document.getElementById('status').textContent='External chart could not load. Check internet/blockers or use the local chart / Open on TradingView link.';};
 script.onload=()=>{document.getElementById('status').textContent='Chart script loaded. Verify symbol and delay in the provider frame. Internal symbol changes do NOT change local analysis or SPX GEX.';};
 container.appendChild(script);document.getElementById('widget').appendChild(container);
 setTimeout(()=>{if(!container.querySelector('iframe'))document.getElementById('status').textContent='No external chart frame detected. Some symbols/exchanges are unavailable in free embeds. Use the direct link or local data mode.';},15000);
})();

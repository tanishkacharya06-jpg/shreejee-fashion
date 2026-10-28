(function(){
  function taskbarIsHidden(){try{return localStorage.getItem('sf_taskbar_hidden')==='1';}catch(_){return false;}}
  function syncTaskbarVisibility(){
    const hidden=taskbarIsHidden();
    document.documentElement.classList.toggle('sf-taskbar-hidden',hidden);
    document.querySelectorAll('[data-taskbar-visibility]').forEach(input=>{input.checked=!hidden;input.setAttribute('aria-label',hidden?'Show bottom taskbar':'Hide bottom taskbar');});
  }
  syncTaskbarVisibility();
  document.addEventListener('change',function(event){
    const input=event.target.closest('[data-taskbar-visibility]');if(!input)return;
    try{localStorage.setItem('sf_taskbar_hidden',input.checked?'0':'1');}catch(_){}
    syncTaskbarVisibility();
  });
  function showCartToast(message, bad){
    let toast=document.getElementById('cartAjaxToast');
    if(!toast){toast=document.createElement('div');toast.id='cartAjaxToast';toast.className='cart-ajax-toast';toast.setAttribute('role','status');toast.setAttribute('aria-live','polite');document.body.appendChild(toast);}
    toast.replaceChildren();
    const mark=document.createElement('span');mark.className='cart-toast-mark'+(bad?' is-error':'');mark.textContent=bad?'!':'✓';mark.setAttribute('aria-hidden','true');
    const text=document.createElement('span');text.className='cart-toast-text';text.textContent=message;
    toast.append(mark,text);toast.classList.toggle('is-error',!!bad);toast.classList.add('is-visible');
    clearTimeout(window.__cartToastTimer);window.__cartToastTimer=setTimeout(()=>toast.classList.remove('is-visible'),2600);
  }
  function showAuthGate(data){
    document.getElementById('cartAuthGate')?.remove();
    const overlay=document.createElement('div');overlay.id='cartAuthGate';overlay.className='auth-gate-overlay';
    overlay.innerHTML=`<section class="auth-gate-modal" role="dialog" aria-modal="true" aria-labelledby="cartAuthTitle"><button type="button" class="auth-gate-close" aria-label="Close">×</button><div class="eyebrow">MEMBER ACCESS</div><h2 id="cartAuthTitle">One quick step before your cart.</h2><p>${escapeHtml(data.message||'Log in or create an account to add this product. Your selected item will be added after authentication.')}</p><div class="auth-gate-actions"><a class="btn btn-dark" id="cartGateLogin">Log in</a><a class="btn btn-outline" id="cartGateRegister">Create account</a></div><small>Your selected item is saved temporarily and will be added once after you sign in.</small></section>`;
    overlay.querySelector('#cartGateLogin').href=data.login_url;
    overlay.querySelector('#cartGateRegister').href=data.register_url;
    overlay.querySelector('.auth-gate-close').addEventListener('click',()=>overlay.remove());
    overlay.addEventListener('click',e=>{if(e.target===overlay)overlay.remove();});
    document.addEventListener('keydown',function onEsc(e){if(e.key==='Escape'){overlay.remove();document.removeEventListener('keydown',onEsc);}});
    document.body.appendChild(overlay);overlay.querySelector('#cartGateLogin').focus();
  }
  function escapeHtml(value){return String(value).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}
  // Eight locally generated sound effects, including taskbar/navigation links.
  let sfSoundContext=null;
  function buttonSoundsEnabled(){try{return localStorage.getItem('sf_button_sounds')!=='off';}catch(_){return true;}}
  function selectedButtonSound(){try{return localStorage.getItem('sf_button_sound_style')||'soft';}catch(_){return 'soft';}}
  function selectedButtonSoundVolume(){try{const raw=localStorage.getItem('sf_button_sound_volume');if(raw===null||raw==='')return 100;const n=Number(raw);return Number.isFinite(n)?Math.max(0,Math.min(100,n)):100;}catch(_){return 100;}}
  window.__sfSelectedButtonSound=selectedButtonSound;window.__sfSelectedButtonSoundVolume=selectedButtonSoundVolume;window.__sfPlayButtonSound=playButtonSound;
  function playButtonSound(styleOverride){
    if(!styleOverride&&!buttonSoundsEnabled())return;
    const style=styleOverride||selectedButtonSound();
    const intensity=selectedButtonSoundVolume()/100;if(intensity<=0)return;
    const Ctx=window.AudioContext||window.webkitAudioContext;if(!Ctx)return;
    try{
      if(!sfSoundContext)sfSoundContext=new Ctx();
      const ctx=sfSoundContext;
      const play=()=>{
        const now=ctx.currentTime;
        const tone=(freq,endFreq,duration,type='sine',start=0,volume=.045)=>{
          const osc=ctx.createOscillator(),gain=ctx.createGain(),t=now+start;
          osc.type=type;osc.frequency.setValueAtTime(Math.max(1,freq),t);
          if(endFreq&&endFreq!==freq)osc.frequency.exponentialRampToValueAtTime(Math.max(1,endFreq),t+duration*.78);
          // Non-linear volume mapping keeps low slider levels audible while 100% is deliberately clear.
          const audibleVolume=Math.min(.98,volume*15*Math.pow(intensity,.58));
          gain.gain.setValueAtTime(.0001,t);gain.gain.exponentialRampToValueAtTime(Math.max(.0003,audibleVolume),t+.006);gain.gain.exponentialRampToValueAtTime(.0001,t+duration);
          osc.connect(gain);gain.connect(ctx.destination);osc.start(t);osc.stop(t+duration+.01);
        };
        switch(style){
          case 'pop': tone(340,820,.105,'triangle',0,.07);break;
          case 'chime': tone(760,760,.13,'sine',0,.055);tone(1040,1040,.15,'sine',.075,.045);break;
          case 'bubble': tone(250,680,.15,'sine',0,.065);break;
          case 'bell': tone(1050,910,.21,'sine',0,.065);tone(1570,1320,.17,'sine',.008,.035);break;
          case 'sparkle': tone(880,880,.085,'sine',0,.05);tone(1180,1180,.09,'sine',.055,.05);tone(1570,1570,.12,'sine',.11,.045);break;
          case 'arcade': tone(440,660,.075,'square',0,.035);tone(660,880,.08,'square',.065,.03);break;
          case 'wood': tone(230,145,.075,'triangle',0,.065);break;
          case 'soft':default:tone(610,430,.085,'sine',0,.05);break;
        }
      };
      if(ctx.state==='suspended'){ctx.resume().then(()=>{try{play();}catch(_){}}).catch(()=>{});}else{play();}
    }catch(_){/* Audio is optional; interface actions always continue. */}
  }
  function soundControlFrom(target){return target?.closest?.('button, a[href], input[type="submit"], input[type="button"], input[type="reset"], input[type="checkbox"], input[type="radio"], select, summary, .btn, [role="button"], [data-category-filter], [data-category-submenu-toggle], .category-tile, .filter-pills a, .ui-bottombar a');}
  let lastPointerSoundControl=null,lastPointerSoundAt=0;
  // pointerdown makes short sounds start before a taskbar link navigates to another page.
  document.addEventListener('pointerdown',function(event){
    if(event.button!==0||!buttonSoundsEnabled())return;
    const control=soundControlFrom(event.target);if(!control||control.disabled||control.getAttribute('aria-disabled')==='true'||control.dataset.soundIgnore==='1')return;
    if(event.target.closest('[data-sound-preview]'))return;
    playButtonSound();lastPointerSoundControl=control;lastPointerSoundAt=performance.now();
  },true);
  document.addEventListener('click',function(event){
    if(!buttonSoundsEnabled())return;
    const control=soundControlFrom(event.target);if(!control||control.disabled||control.getAttribute('aria-disabled')==='true'||control.dataset.soundIgnore==='1')return;
    if(event.target.closest('[data-sound-preview]'))return;
    if(control===lastPointerSoundControl&&performance.now()-lastPointerSoundAt<650){lastPointerSoundControl=null;return;}
    playButtonSound();
  },true);
  function syncButtonSoundControl(){
    const enabled=buttonSoundsEnabled();document.querySelectorAll('[data-button-sounds-toggle]').forEach(input=>{input.checked=enabled;const text=input.parentElement?.querySelector('[data-sound-switch-text]')||input.closest('.ui-sound-switch')?.querySelector('.ui-sound-switch-text');if(text)text.textContent=enabled?'On':'Off';input.setAttribute('aria-label',enabled?'Turn button sound effects off':'Turn button sound effects on');});
    document.querySelectorAll('[data-button-sound-style]').forEach(select=>{select.value=selectedButtonSound();});
    document.querySelectorAll('[data-button-sound-volume]').forEach(input=>{input.value=String(selectedButtonSoundVolume());const label=input.closest('.ui-sound-volume-setting')?.querySelector('[data-sound-volume-label]')||document.querySelector('[data-sound-volume-label]');if(label)label.textContent=selectedButtonSoundVolume()+'%';});
  }
  syncButtonSoundControl();
  document.addEventListener('change',function(event){
    const toggle=event.target.closest('[data-button-sounds-toggle]');if(toggle){try{localStorage.setItem('sf_button_sounds',toggle.checked?'on':'off');}catch(_){}syncButtonSoundControl();return;}
    const style=event.target.closest('[data-button-sound-style]');if(style){try{localStorage.setItem('sf_button_sound_style',style.value);}catch(_){}return;}
    const volume=event.target.closest('[data-button-sound-volume]');if(volume){try{localStorage.setItem('sf_button_sound_volume',String(Math.max(0,Math.min(100,Number(volume.value)||0))));}catch(_){}syncButtonSoundControl();return;}
  });
  document.addEventListener('input',function(event){const volume=event.target.closest('[data-button-sound-volume]');if(!volume)return;const n=Math.max(0,Math.min(100,Number(volume.value)||0));const label=volume.closest('.ui-sound-volume-setting')?.querySelector('[data-sound-volume-label]')||document.querySelector('[data-sound-volume-label]');if(label)label.textContent=n+'%';try{localStorage.setItem('sf_button_sound_volume',String(n));}catch(_){}});
  document.addEventListener('click',function(event){const preview=event.target.closest('[data-sound-preview]');if(!preview)return;playButtonSound(selectedButtonSound());});
  // Animated app drawer: works on desktop and phones, closes with Escape or backdrop.
  const drawerOverlay=document.getElementById('appDrawerOverlay');
  const drawer=document.getElementById('appDrawer');
  const drawerTriggers=[document.getElementById('appDrawerToggle'),document.getElementById('mobileDrawerToggle')].filter(Boolean);
  let drawerPreviousFocus=null,drawerCloseTimer=null;
  function openAppDrawer(){
    if(!drawerOverlay||!drawer)return;clearTimeout(drawerCloseTimer);drawerPreviousFocus=document.activeElement;drawerOverlay.hidden=false;
    document.body.classList.add('app-drawer-open');drawerTriggers.forEach(b=>b.setAttribute('aria-expanded','true'));
    requestAnimationFrame(()=>{drawerOverlay.classList.add('is-open');drawer.focus();});
  }
  function closeAppDrawer(){
    if(!drawerOverlay||drawerOverlay.hidden)return;drawerOverlay.classList.remove('is-open');document.body.classList.remove('app-drawer-open');drawerTriggers.forEach(b=>b.setAttribute('aria-expanded','false'));
    drawerCloseTimer=setTimeout(()=>{drawerOverlay.hidden=true;drawerPreviousFocus?.focus?.();},220);
  }
  document.querySelector('#siteAnnouncement summary')?.addEventListener('click',e=>{if(document.getElementById('siteAnnouncement').hasAttribute('data-short'))e.preventDefault();});
  drawerTriggers.forEach(b=>b.addEventListener('click',openAppDrawer));
  drawerOverlay?.querySelectorAll('[data-close-app-drawer]').forEach(b=>b.addEventListener('click',closeAppDrawer));
  drawer?.querySelectorAll('a').forEach(a=>a.addEventListener('click',closeAppDrawer));
  // Desktop dropdowns open on hover/focus and also toggle cleanly on tap/click.
  document.querySelectorAll('[data-nav-dropdown-toggle]').forEach(toggle=>{
    toggle.addEventListener('click',event=>{
      event.preventDefault();
      const current=toggle.closest('[data-nav-dropdown]');
      const open=!current.classList.contains('is-open');
      document.querySelectorAll('[data-nav-dropdown].is-open').forEach(node=>{
        node.classList.remove('is-open');node.querySelector('[data-nav-dropdown-toggle]')?.setAttribute('aria-expanded','false');
      });
      if(open){current.classList.add('is-open');toggle.setAttribute('aria-expanded','true');}
    });
  });
  document.addEventListener('click',event=>{
    const categoryToggle=event.target.closest('[data-category-submenu-toggle]');
    if(categoryToggle){
      event.preventDefault();const group=categoryToggle.closest('[data-category-submenu]');const open=!group.classList.contains('is-open');
      group.parentElement?.querySelectorAll('[data-category-submenu].is-open').forEach(node=>{node.classList.remove('is-open');node.querySelector('[data-category-submenu-toggle]')?.setAttribute('aria-expanded','false');});
      if(open){group.classList.add('is-open');categoryToggle.setAttribute('aria-expanded','true');}else{categoryToggle.setAttribute('aria-expanded','false');}
      return;
    }
    if(event.target.closest('[data-nav-dropdown]'))return;
    document.querySelectorAll('[data-nav-dropdown].is-open').forEach(node=>{
      node.classList.remove('is-open');node.querySelector('[data-nav-dropdown-toggle]')?.setAttribute('aria-expanded','false');
    });
    document.querySelectorAll('[data-category-submenu].is-open').forEach(node=>{node.classList.remove('is-open');node.querySelector('[data-category-submenu-toggle]')?.setAttribute('aria-expanded','false');});
  });
  document.addEventListener('keydown',event=>{
    if(event.key!=='Escape')return;
    document.querySelectorAll('[data-nav-dropdown].is-open').forEach(node=>{
      node.classList.remove('is-open');node.querySelector('[data-nav-dropdown-toggle]')?.setAttribute('aria-expanded','false');node.querySelector('[data-nav-dropdown-toggle]')?.blur();
    });
    document.querySelectorAll('[data-category-submenu].is-open').forEach(node=>{node.classList.remove('is-open');node.querySelector('[data-category-submenu-toggle]')?.setAttribute('aria-expanded','false');});
  });
  // Fresh Pop dock behavior is initialized at the end of this file with small-delta handling.
  document.addEventListener('keydown',e=>{if(e.key==='Escape')closeAppDrawer();});
  function cartBarEnabled(){try{return localStorage.getItem('shreejeeGoToCartBar')!=='off';}catch(e){return true;}}
  function setCartBarVisibility(show){const bar=document.getElementById('goToCartBar');if(bar)bar.hidden=!show;}
  function showGoToCartBar(count){if(!cartBarEnabled())return;const bar=document.getElementById('goToCartBar');if(!bar)return;const countNode=bar.querySelector('[data-go-cart-count]');if(countNode)countNode.textContent=String(count||'');bar.hidden=false;}
  const goCartHide=document.getElementById('hideGoToCartBar');goCartHide?.addEventListener('click',()=>{try{localStorage.setItem('shreejeeGoToCartBar','off');}catch(e){}setCartBarVisibility(false);});
  const cartPrefToggle=document.getElementById('cartBarPreferenceToggle');cartPrefToggle?.addEventListener('click',()=>{const next=!cartBarEnabled();try{localStorage.setItem('shreejeeGoToCartBar',next?'on':'off');}catch(e){}if(next){const count=Number(document.querySelector('.nav-cart .cart-quantity')?.textContent||0);showGoToCartBar(count);}else setCartBarVisibility(false);cartPrefToggle.textContent=next?'Hide Go To Cart bar':'Show Go To Cart bar';});
  if(cartPrefToggle)cartPrefToggle.textContent=cartBarEnabled()?'Hide Go To Cart bar':'Show Go To Cart bar';
  function setCartCount(n){
    document.querySelectorAll('.cart-quantity').forEach(node=>{node.textContent=String(n);});
    document.querySelectorAll('[data-cart-badge]').forEach(node=>{node.textContent=String(n);node.hidden=!Number(n);});
  }
  function animateToCart(form){
    if(window.matchMedia('(prefers-reduced-motion: reduce)').matches)return;
    const source=form.closest('.product-card')?.querySelector('.product-image img')||form.querySelector('button[type="submit"]');
    const isVisible=el=>{if(!el)return false;const r=el.getBoundingClientRect();return r.width>0&&r.height>0;};
    // On phones the Cart link is hidden inside the menu, so fly to the visible Menu button (never to the top-left corner).
    const target=[document.querySelector('.nav-cart'),document.querySelector('.header-cart'),document.querySelector('.ui-bb-cart'),document.getElementById('appDrawerToggle')].find(isVisible);if(!source||!target||!source.getBoundingClientRect)return;
    const a=source.getBoundingClientRect(),b=target.getBoundingClientRect();
    const clone=document.createElement('div');clone.className='cart-fly-clone';
    const img=source.tagName==='IMG'?source.cloneNode(true):null;
    if(img){img.removeAttribute('loading');clone.appendChild(img);}else clone.textContent='＋';
    Object.assign(clone.style,{position:'fixed',left:(a.left+a.width/2-24)+'px',top:(a.top+a.height/2-24)+'px',width:'48px',height:'48px',zIndex:'9999',pointerEvents:'none',borderRadius:'12px',overflow:'hidden',background:'#e9e2d4',boxShadow:'0 6px 22px rgba(0,0,0,.18)',display:'grid',placeItems:'center'});
    if(img)Object.assign(img.style,{width:'100%',height:'100%',objectFit:'cover'});
    document.body.appendChild(clone);
    const tx=Math.min(Math.max(b.left+b.width/2,16),window.innerWidth-16),ty=Math.min(Math.max(b.top+b.height/2,16),window.innerHeight-16);const dx=tx-(a.left+a.width/2),dy=ty-(a.top+a.height/2);
    const animation=clone.animate([{transform:'translate(0,0) scale(1)',opacity:1},{transform:`translate(${dx}px,${dy}px) scale(.12)`,opacity:.25}],{duration:580,easing:'cubic-bezier(.2,.75,.25,1)'});
    animation.onfinish=()=>clone.remove();
    target.classList.remove('cart-icon-bump');void target.offsetWidth;target.classList.add('cart-icon-bump');setTimeout(()=>target.classList.remove('cart-icon-bump'),650);
  }
  document.addEventListener('submit', async function(event){
    const form=event.target.closest('form.js-add-to-cart');if(!form)return;
    event.preventDefault();event.stopImmediatePropagation();
    if(form.dataset.adding==='1')return;
    if(!form.checkValidity()){form.reportValidity();return;}
    form.dataset.adding='1';
    const button=form.querySelector('button[type="submit"]');const previous=button?button.innerHTML:'';
    if(button){button.disabled=true;button.innerHTML='Adding…';}
    try{
      const response=await fetch(form.action,{method:'POST',body:new FormData(form),headers:{'X-Requested-With':'XMLHttpRequest','Accept':'application/json'},credentials:'same-origin'});
      const data=await response.json().catch(()=>({ok:false,message:'The store returned an unexpected response. Please try again.'}));
      if(response.status===401&&data.auth_required){showAuthGate(data);return;}
      if(response.status===422&&data.needs_size){
        if(button){button.innerHTML=previous;button.disabled=false;}form.dataset.adding='0';
        askSize(data,size=>{const fname=data.need==='colour'?'colour':'size';let f=form.querySelector('input[name="'+fname+'"]');if(!f){f=document.createElement('input');f.type='hidden';f.name=fname;f.dataset.dynamicSize='1';form.appendChild(f);}f.value=size;if(fname==='size')document.querySelectorAll('[data-size-picker] .size-chip').forEach(c=>c.classList.toggle('active',c.dataset.size===size));else document.querySelectorAll('[data-colour-picker] .size-chip').forEach(c=>c.classList.toggle('active',c.dataset.colour===size));form.requestSubmit();});
        return;
      }
      if(!response.ok||!data.ok)throw new Error(data.message||'Could not add this product.');
      form.querySelectorAll('input[data-dynamic-size]').forEach(n=>n.remove());
      setCartCount(data.cart_count);
      if(button){button.innerHTML=button.classList.contains('add-btn')?'<span class="add-success-mark" aria-hidden="true">✓</span>':'<span class="add-success-mark" aria-hidden="true">✓</span> Added';button.classList.add('added-to-cart');button.setAttribute('aria-label','Added to cart successfully');}
      // Only the tick on the button plays; just surface notes such as quantity limits.
      if(/only|limit/i.test(data.message||''))showCartToast(data.message);
      window.dispatchEvent(new CustomEvent('shreejee:cart-updated',{detail:data}));
      setTimeout(()=>{if(button){button.innerHTML=previous;button.classList.remove('added-to-cart');button.disabled=false;button.removeAttribute('aria-label');}form.dataset.adding='0';},900);
    }catch(err){showCartToast(err.message||'Could not add to cart. Please try again.',true);}
    finally{if(form.dataset.adding==='1'){setTimeout(()=>{if(button){button.innerHTML=previous;button.classList.remove('added-to-cart');button.disabled=false;button.removeAttribute('aria-label');}form.dataset.adding='0';},900);}}
  },true);

  function askSize(data,onPick){
    document.querySelector('.size-modal')?.remove();
    const o=document.createElement('div');o.className='size-modal';
    o.innerHTML='<div class="size-modal-card" role="dialog" aria-modal="true" aria-label="Choose an option"><button type="button" class="size-modal-close" aria-label="Close">×</button><div class="eyebrow">CHOOSE YOUR '+(data.need==='colour'?'COLOUR':'SIZE')+'</div><h3>'+escapeHtml(data.product_name||'Select a size')+'</h3><div class="size-chips">'+((data.need==='colour'?data.colours:data.sizes)||[]).map(s=>'<button type="button" class="size-chip" data-size="'+escapeHtml(s)+'">'+escapeHtml(s)+'</button>').join('')+'</div><p class="small muted">Tap an option to add this item to your cart.</p></div>';
    const close=()=>{o.remove();document.removeEventListener('keydown',onKey);};
    const onKey=e=>{if(e.key==='Escape')close();};
    o.addEventListener('click',e=>{if(e.target===o||e.target.closest('.size-modal-close')){close();return;}const chip=e.target.closest('.size-chip');if(chip){close();onPick(chip.dataset.size);}});
    document.addEventListener('keydown',onKey);document.body.appendChild(o);o.querySelector('.size-chip')?.focus();
  }

  // Save for later (heart) on cards, product page and wishlist page.
  document.addEventListener('submit',async function(event){
    const form=event.target.closest('form.js-wishlist');if(!form)return;
    event.preventDefault();if(form.dataset.busy==='1')return;form.dataset.busy='1';
    const btn=form.querySelector('button[type="submit"]');
    try{
      const res=await fetch(form.action,{method:'POST',body:new FormData(form),headers:{'X-Requested-With':'XMLHttpRequest','Accept':'application/json'},credentials:'same-origin'});
      const data=await res.json().catch(()=>({ok:false,message:'Please try again.'}));
      if(res.status===401&&data.auth_required){showAuthGate(data);return;}
      if(!res.ok||!data.ok)throw new Error(data.message||'Could not update saved items.');
      if(btn){
        if(btn.classList.contains('wish-btn')){btn.classList.toggle('saved',data.saved);btn.textContent=data.saved?'♥':'♡';}
        else btn.textContent=data.saved?(btn.dataset.savedLabel||'♥ Saved'):(btn.dataset.unsavedLabel||'♡ Save for later');
        btn.setAttribute('aria-pressed',data.saved?'true':'false');
      }
      document.querySelectorAll('[data-wishlist-count]').forEach(n=>{n.textContent=data.count;n.hidden=!data.count;});
      document.querySelectorAll('[data-wishlist-count-text]').forEach(n=>{n.textContent=data.count;});
      showCartToast(data.message);
      if(!data.saved&&document.querySelector('[data-wishlist-page]')){form.closest('.product-card')?.remove();if(!document.querySelector('[data-wishlist-page] .product-card'))location.reload();}
    }catch(err){showCartToast(err.message||'Could not update saved items.',true);}
    finally{form.dataset.busy='0';}
  });

  // Back button: go to the previous page when there is one on this site, otherwise use its fallback link.
  document.addEventListener('click',function(e){
    const back=e.target.closest('[data-back]');if(!back||e.metaKey||e.ctrlKey||e.shiftKey||e.button)return;
    let sameSite=false;try{sameSite=!!document.referrer&&new URL(document.referrer).origin===location.origin;}catch(_){}
    if(sameSite&&history.length>1){e.preventDefault();history.back();}
  });

  // Keep order status fields synchronized with server-side state without a full-page refresh.
  async function refreshOrderStatus(){
    const nodes=[...document.querySelectorAll('[data-order-status][data-status-url]')];
    const urls=[...new Set(nodes.map(n=>n.dataset.statusUrl).filter(Boolean))];
    await Promise.all(urls.map(async url=>{
      try{
        const res=await fetch(url,{headers:{'Accept':'application/json'},cache:'no-store',credentials:'same-origin'});if(!res.ok)return;
        const data=await res.json();if(!data.ok)return;
        // The order page is customer-specific: reload it as soon as its quote, total, or status changes.
        // This makes a newly sent delivery fee visible immediately without asking the customer to refresh.
        const orderPageRoot=document.getElementById('orderProgressPanel');
        if(orderPageRoot){
          const initialStatus=orderPageRoot.dataset.initialStatus||'';
          const initialTotal=Number(orderPageRoot.dataset.initialTotal||0).toFixed(2);
          const liveTotal=Number(data.total||0).toFixed(2);
          const initialUpdated=orderPageRoot.dataset.initialUpdatedAt||'';
          const initialFeeStatus=orderPageRoot.dataset.initialFeeStatus||'';
          const liveFeeStatus=data.delivery_charge_status||'';
          if(data.status!==initialStatus||liveTotal!==initialTotal||(data.updated_at&&data.updated_at!==initialUpdated)||liveFeeStatus!==initialFeeStatus){window.location.reload();return;}
        }
        // My Account includes order totals too, so reload that page if an order receives a new quote.
        const accountRow=document.querySelector(`[data-status-url="${CSS.escape(url)}"]`)?.closest('tr');
        if(accountRow){
          const rowNode=accountRow.querySelector('[data-order-status]');
          if(rowNode){
            const changed=(rowNode.dataset.initialStatus&&rowNode.dataset.initialStatus!==data.status)||
              (rowNode.dataset.initialTotal&&Number(rowNode.dataset.initialTotal).toFixed(2)!==Number(data.total||0).toFixed(2))||
              (rowNode.dataset.initialUpdatedAt&&data.updated_at&&rowNode.dataset.initialUpdatedAt!==data.updated_at)||
              (rowNode.dataset.initialFeeStatus&&(rowNode.dataset.initialFeeStatus!==(data.delivery_charge_status||'')));
            if(changed){window.location.reload();return;}
          }
        }
        document.querySelectorAll(`[data-status-url="${CSS.escape(url)}"]`).forEach(node=>{
          if(node.textContent.trim()!==data.status){node.textContent=data.status;node.classList.add('status-refresh-pulse');setTimeout(()=>node.classList.remove('status-refresh-pulse'),450);}
          const parent=node.closest('.ongoing-order-card,.last-order-content,.order-confirm-grid,.account-grid');const pay=parent?.querySelector('[data-order-payment]');if(pay)pay.textContent=data.payment_status;
        });
        const doneState=['Delivered','Completed','Cancelled'].includes(data.status);const etaText=data.eta||'';
        const etaBox=document.getElementById('orderEtaBox');if(etaBox){const t=document.getElementById('orderEtaText');if(t)t.textContent=etaText;etaBox.hidden=!etaText||doneState;}
        document.querySelectorAll(`[data-eta-url="${CSS.escape(url)}"]`).forEach(node=>{const strong=node.querySelector('strong');if(strong)strong.textContent=etaText;node.hidden=!etaText||doneState;});
        const codeBox=document.getElementById('orderConfirmationCodeBox');
        const acceptedBox=document.getElementById('orderConfirmationAccepted');
        const codeNode=document.getElementById('orderConfirmationCode');
        const activeCode=['COD','Online'].includes(data.payment_method)&&data.status==='Awaiting delivery code'&&!!data.confirmation_code&&!data.confirmation_code_used;
        if(codeBox){codeBox.hidden=!activeCode;if(codeNode)codeNode.textContent=activeCode?data.confirmation_code:'';}
        if(acceptedBox)acceptedBox.hidden=!(data.status==='Delivered'||data.status==='Completed');
        const upiPanel=document.getElementById('upiPaymentBlock');const upiReported=document.getElementById('upiReportedBlock');
        if(upiPanel)upiPanel.hidden=!(data.payment_method==='Online'&&data.status==='Awaiting UPI payment');
        if(upiReported)upiReported.hidden=!(data.payment_method==='Online'&&data.status==='UPI payment reported');
        if(data.status==='Delivered'||data.status==='Completed'){
          const heading=document.getElementById('orderProgressHeading');if(heading)heading.textContent='You have received your order.';
          const copy=document.getElementById('orderProgressCopy');if(copy)copy.textContent='Your delivery is complete. This order is now locked.';
          const qr=document.getElementById('orderQrBlock');if(qr)qr.hidden=true;
          const tokenBlock=document.getElementById('orderTokenBlock');if(tokenBlock)tokenBlock.hidden=true;
          document.getElementById('customerCancelForm')?.remove();
          if(upiPanel)upiPanel.hidden=true;if(upiReported)upiReported.hidden=true;
          if(codeBox)codeBox.hidden=true;
          document.querySelectorAll(`[data-status-url="${CSS.escape(url)}"]`).forEach(node=>{
            const card=node.closest('.ongoing-order-card');
            if(card){const list=card.parentElement;card.remove();const panel=list?.closest('.ongoing-orders');if(panel&&!list.querySelector('.ongoing-order-card')){panel.querySelector('.mini-status')?.remove();list.innerHTML='<div class="empty-inline"><p>No ongoing orders. Your completed orders remain available in order history.</p></div>';}}
          });
        }else if(data.status==='Cancelled'){
          document.getElementById('customerCancelForm')?.remove();
          const heading=document.getElementById('orderProgressHeading');if(heading)heading.textContent='This order has been cancelled.';
          const copy=document.getElementById('orderProgressCopy');if(copy)copy.textContent='The cancelled order will remain in your order history.';
          const qr=document.getElementById('orderQrBlock');if(qr)qr.hidden=true;if(codeBox)codeBox.hidden=true;
          document.querySelectorAll(`[data-status-url="${CSS.escape(url)}"]`).forEach(node=>node.closest('.ongoing-order-card')?.remove());
        }else if(data.status==='Awaiting delivery code'){
          document.getElementById('customerCancelForm')?.remove();
          const heading=document.getElementById('orderProgressHeading');if(heading)heading.textContent='Confirming your delivery.';
          const copy=document.getElementById('orderProgressCopy');if(copy)copy.textContent='Your delivery person has arrived. Share the six-digit code only when receiving the item.';
          const qr=document.getElementById('orderQrBlock');if(qr)qr.hidden=true;
        }else if(data.status==='On the way'){
          document.getElementById('customerCancelForm')?.remove();
          const heading=document.getElementById('orderProgressHeading');if(heading)heading.textContent='Your order is on the way.';
        }else if(data.status==='UPI payment reported'){
          const heading=document.getElementById('orderProgressHeading');if(heading)heading.textContent='Payment report sent to the store.';
          const copy=document.getElementById('orderProgressCopy');if(copy)copy.textContent='The store must verify the transfer before registering your order.';
          const qr=document.getElementById('orderQrBlock');if(qr)qr.hidden=true;
        }else if(data.status==='Awaiting UPI payment'){
          const heading=document.getElementById('orderProgressHeading');if(heading)heading.textContent='Complete your UPI payment.';
          const qr=document.getElementById('orderQrBlock');if(qr)qr.hidden=true;
        }else if(data.status==='Order confirmed'){
          const heading=document.getElementById('orderProgressHeading');if(heading)heading.textContent='Your order is confirmed.';
          const copy=document.getElementById('orderProgressCopy');if(copy)copy.textContent='Your payment has been verified by the store and your order is confirmed. Packing and delivery updates will appear here automatically.';
        }else if(['COD requested','Preparing item','Ready for dispatch','On the way'].includes(data.status)&&['COD','Online'].includes(data.payment_method)){
          const heading=document.getElementById('orderProgressHeading');if(heading)heading.textContent='Your order is being prepared for delivery.';
          const copy=document.getElementById('orderProgressCopy');if(copy)copy.textContent='Track your order here. The store status updates automatically.';
        }
      }catch(_){/* temporary network issue; next poll retries */}
    }));
  }
  refreshOrderStatus();setInterval(()=>{if(document.visibilityState==='visible')refreshOrderStatus();},6000);

  // Admin order queues refresh automatically when a customer places an order or an existing order changes.
  (function watchAdminOrders(){
    const marker=document.getElementById('adminOrderSnapshot');
    const isAdmin=document.body?.dataset.userRole==='admin';
    if(!marker&&!isAdmin)return;
    // The base template includes a render-time snapshot on every admin page.
    const base=(marker?.dataset.value||document.body?.dataset.adminOrderSnapshot||'');
    if(!base)return;
    async function check(){
      if(document.visibilityState!=='visible')return;
      try{
        const response=await fetch('/api/order-updates',{cache:'no-store',credentials:'same-origin',headers:{'Accept':'application/json'}});
        if(!response.ok)return;
        const data=await response.json();
        if(data.ok&&data.snapshot&&data.snapshot!==base){window.location.reload();}
      }catch(_){/* retry next poll */}
    }
    setInterval(check,6000);
    setTimeout(check,500);
  })();

  // Published website-editor settings and catalog changes arrive without reloading customer pages.
  let siteSnapshot='';
  let appliedSiteName=document.querySelector('.brand-name [data-site-key="site_name"]')?.textContent.trim()||'Shreejee Fashion';
  function applySiteConfig(config){
    const ann=document.getElementById('siteAnnouncement');if(ann&&'announcement' in config){const v=String(config.announcement||'').trim();ann.hidden=!v;ann.toggleAttribute('data-short',v.length<=96);if(v.length<=96)ann.open=false;}
    document.querySelectorAll('[data-site-key]').forEach(node=>{
      const key=node.dataset.siteKey;if(!(key in config)||config[key]===null||config[key]===undefined)return;
      let value=String(config[key]);if(node.classList.contains('announcement-text')&&value.length>96)value=value.slice(0,95).trimEnd()+'…';
      if(node.textContent!==value)node.textContent=value;
      const hrefKey=node.dataset.siteHrefKey;
      if(hrefKey&&config[hrefKey])node.href=hrefKey==='phone'?'tel:'+String(config.phone||''):String(config[hrefKey]);
    });
    const root=document.documentElement.style;
    const variableMap={primary_color:'--green',accent_color:'--orange',text_color:'--ink',page_background:'--cream',card_background:'--paper'};
    Object.entries(variableMap).forEach(([key,variable])=>{if(config[key])root.setProperty(variable,config[key]);});
    if(config.font_family){const fonts=['DM Sans','Arial','Georgia','Trebuchet MS','Verdana','Tahoma','system-ui'];if(fonts.includes(config.font_family))root.setProperty('--sans','"'+config.font_family+'",system-ui,sans-serif');}
    if(config.button_radius!==undefined)root.setProperty('--button-radius',Math.max(0,Math.min(30,Number(config.button_radius)||0))+'px');
    if(config.card_radius!==undefined)root.setProperty('--card-radius',Math.max(0,Math.min(36,Number(config.card_radius)||0))+'px');
    if(config.card_gap!==undefined)root.setProperty('--card-gap',Math.max(8,Math.min(40,Number(config.card_gap)||22))+'px');
    if(config.base_font_size!==undefined)root.setProperty('--base-font-size',Math.max(12,Math.min(18,Number(config.base_font_size)||14))+'px');
    if(config.heading_scale!==undefined)root.setProperty('--heading-scale',Math.max(.8,Math.min(1.3,Number(config.heading_scale)||1)));
    if(config.gradient_end)root.setProperty('--gradient-end',config.gradient_end);
    document.body.classList.remove('button-style-solid','button-style-outline','button-style-soft');document.body.classList.add('button-style-'+(['solid','outline','soft'].includes(config.button_style)?config.button_style:'solid'));
    if(config.background_mode==='soft-gradient'&&config.page_background&&config.gradient_end)document.body.style.background=`linear-gradient(135deg,${config.page_background},${config.gradient_end}) fixed`;else document.body.style.background=config.page_background||'';
    if(config.site_name&&config.site_name!==appliedSiteName){
      const splitAt=document.title.lastIndexOf('|');
      if(splitAt>=0){
        const left=document.title.slice(0,splitAt).trim(),right=document.title.slice(splitAt+1).trim();
        // Homepage titles use the store name on the left; detail/account titles use it on the right.
        if(right===appliedSiteName)document.title=left+' | '+config.site_name;
        else if(left===appliedSiteName)document.title=config.site_name+' | '+right;
      }
      appliedSiteName=config.site_name;
    }
    const brandMark=document.querySelector('.brand-mark');
    if(brandMark){
      if(config.logo_filename){const src='/static/uploads/'+encodeURIComponent(config.logo_filename);let img=brandMark.querySelector('img');if(!img){brandMark.textContent='';img=document.createElement('img');img.alt=(config.site_name||'Store')+' logo';brandMark.appendChild(img);}if(!img.src.endsWith(src))img.src=src;brandMark.classList.add('brand-image');}
      else if(brandMark.querySelector('img')){brandMark.textContent=config.logo_text||'SF';brandMark.classList.remove('brand-image');}
      else if(config.logo_text)brandMark.textContent=config.logo_text;
    }
    const hero=document.querySelector('[data-home-hero]');if(hero)hero.hidden=config.hero_enabled===false;
    const art=document.querySelector('.hero-art');if(art&&config.hero_image)art.style.backgroundImage=`linear-gradient(135deg,rgba(29,48,39,.1),rgba(29,48,39,.02)),url("/static/uploads/${encodeURIComponent(config.hero_image)}")`;
  }
  async function refreshSiteSettings(){const url=document.body.dataset.settingsUrl;if(!url)return;try{const response=await fetch(url,{cache:'no-store',credentials:'same-origin'});if(!response.ok)return;const config=await response.json();const snapshot=JSON.stringify(config);if(snapshot!==siteSnapshot){siteSnapshot=snapshot;applySiteConfig(config);}}catch(_){}}
  refreshSiteSettings();setInterval(()=>{if(document.visibilityState==='visible')refreshSiteSettings();},30000);

  const catalog=document.getElementById('liveCatalogResults');
  async function refreshCatalog(){if(!catalog||!catalog.dataset.catalogUrl)return;try{const response=await fetch(catalog.dataset.catalogUrl,{cache:'no-store',credentials:'same-origin'});if(!response.ok)return;const html=await response.text();if(html.trim()!==catalog.innerHTML.trim())catalog.innerHTML=html;}catch(_){/* reconnect on next interval */}}
  if(catalog){setInterval(()=>{if(document.visibilityState==='visible')refreshCatalog();},30000);}

  // Prevent accidental duplicate form submissions throughout the store.

  document.querySelectorAll('form.js-protect-submit').forEach(form=>{
    form.addEventListener('submit',function(e){
      if(form.dataset.submitting==='1'){e.preventDefault();return;}
      if(!form.checkValidity()){return;}
      form.dataset.submitting='1';
      const btn=form.querySelector('button[type="submit"]');
      if(btn){btn.disabled=true;btn.dataset.originalText=btn.innerHTML;btn.innerHTML='Please wait…';btn.classList.add('is-loading');}
    });
  });
  // Gentle confirmation for actions that change access/visibility.
  document.querySelectorAll('form[data-confirm]').forEach(form=>form.addEventListener('submit',e=>{if(!confirm(form.dataset.confirm))e.preventDefault();}));

  // Separate gallery and camera pickers feed one multi-image upload queue.
  const form=document.getElementById('productUploadForm');
  const input=document.getElementById('productImages');
  const cameraInput=document.getElementById('cameraImages');
  const chooseGallery=document.getElementById('chooseGallery');
  const openCamera=document.getElementById('openCamera');
  const preview=document.getElementById('imagePreview');
  const previewImg=document.getElementById('previewImg');
  const fileName=document.getElementById('fileName');
  const fileSize=document.getElementById('fileSize');
  const clear=document.getElementById('clearImage');
  const progress=document.getElementById('uploadProgress');
  const state=document.getElementById('uploadState');
  const timer=document.getElementById('uploadTimer');
  const fill=document.getElementById('progressFill');
  const percent=document.getElementById('uploadPercent');
  const save=document.getElementById('saveProductBtn');
  let objectUrl=null;
  let selectedUploadFiles=[];
  function readableBytes(n){return n<1024*1024?(n/1024).toFixed(0)+' KB':(n/1024/1024).toFixed(2)+' MB';}
  function selectedFiles(){return selectedUploadFiles.slice();}
  function showFiles(files){
    if(!files.length){if(preview)preview.hidden=true;return;}
    if(objectUrl)URL.revokeObjectURL(objectUrl);objectUrl=URL.createObjectURL(files[0]);
    if(previewImg)previewImg.src=objectUrl;
    if(fileName)fileName.textContent=files.length===1?files[0].name:`${files.length} photos selected`;
    if(fileSize)fileSize.textContent=readableBytes(files.reduce((sum,f)=>sum+f.size,0))+' total';
    if(preview)preview.hidden=false;
  }
  function addUploadFiles(fileList){
    const incoming=Array.from(fileList||[]);if(!incoming.length)return;
    const all=[...selectedUploadFiles];
    for(const file of incoming){
      const ext=(file.name.split('.').pop()||'').toLowerCase();
      const acceptedExts=['jpg','jpeg','png','webp','heic','heif','avif'];
      if((file.type&&!file.type.startsWith('image/'))||!acceptedExts.includes(ext)){
        alert('Please choose a JPG, PNG, WebP or supported phone photo.');return;
      }
      if(file.size>32*1024*1024){alert('Each original photo must be smaller than 32 MB so it can be prepared in your browser.');return;}
      const duplicate=all.some(existing=>existing.name===file.name&&existing.size===file.size&&existing.lastModified===file.lastModified);
      if(!duplicate)all.push(file);
    }
    if(all.length>8){alert('Choose no more than 8 photos per product.');return;}
    selectedUploadFiles=all;
    showFiles(selectedUploadFiles);
    // Clear native file inputs so the same photo can be picked again after removal if needed.
    if(input)input.value='';if(cameraInput)cameraInput.value='';
  }
  input?.addEventListener('change',()=>addUploadFiles(input.files));
  cameraInput?.addEventListener('change',()=>addUploadFiles(cameraInput.files));
  function openNativePicker(element){
    if(!element)return;
    // showPicker works well on supported mobile browsers; fall back to click for older ones.
    // Inside the Android app (SFApp) showPicker() can silently do nothing, so always use click() there.
    if(!window.SFApp){try{if(typeof element.showPicker==='function'){element.showPicker();return;}}catch(_){}}
    element.click();
  }
  chooseGallery?.addEventListener('click',()=>openNativePicker(input));
  openCamera?.addEventListener('click',()=>openNativePicker(cameraInput));
  clear?.addEventListener('click',()=>{selectedUploadFiles=[];if(input)input.value='';if(cameraInput)cameraInput.value='';if(preview)preview.hidden=true;if(objectUrl)URL.revokeObjectURL(objectUrl);objectUrl=null;if(fileName)fileName.textContent='';if(fileSize)fileSize.textContent='';});
  async function compressFile(file){
    if(!file)return file;
    const ext=(file.name.split('.').pop()||'').toLowerCase();
    const serverTypes=['jpg','jpeg','png','webp'];
    const needsConversion=!serverTypes.includes(ext);
    if(!needsConversion&&file.size<450*1024)return file;
    if(!('createImageBitmap' in window)){
      if(needsConversion)throw new Error('This browser cannot convert that phone-photo format. Please choose JPG, PNG or WebP.');
      return file;
    }
    try{
      const bitmap=await createImageBitmap(file);
      const max=1600,scale=Math.min(1,max/Math.max(bitmap.width,bitmap.height));
      const canvas=document.createElement('canvas');
      canvas.width=Math.max(1,Math.round(bitmap.width*scale));canvas.height=Math.max(1,Math.round(bitmap.height*scale));
      const ctx=canvas.getContext('2d',{alpha:false});
      if(!ctx){bitmap.close?.();if(needsConversion)throw new Error('Could not prepare this photo. Please choose JPG or PNG.');return file;}
      ctx.fillStyle='#ffffff';ctx.fillRect(0,0,canvas.width,canvas.height);ctx.drawImage(bitmap,0,0,canvas.width,canvas.height);bitmap.close?.();
      const blob=await new Promise(resolve=>canvas.toBlob(resolve,'image/jpeg',.82));
      if(!blob){if(needsConversion)throw new Error('Your browser could not convert this phone photo. Please choose JPG, PNG or WebP.');return file;}
      const output=new File([blob],file.name.replace(/\.[^.]+$/,'')+'.jpg',{type:'image/jpeg',lastModified:Date.now()});
      return (needsConversion||output.size<file.size||file.size>8*1024*1024)?output:file;
    }catch(err){
      if(needsConversion)throw new Error('Could not read this phone-photo format. Please choose JPG, PNG or WebP and try again.');
      return file;
    }
  }
  form?.addEventListener('submit',async function(e){
    if(form.dataset.ajaxDone==='1')return;e.preventDefault();if(form.dataset.submitting==='1')return;if(!form.checkValidity()){form.reportValidity();return;}form.dataset.submitting='1';
    if(save){save.disabled=true;save.dataset.originalText=save.innerHTML;save.innerHTML='Preparing photos…';}
    if(progress)progress.hidden=false;if(state)state.textContent='Preparing photos…';if(fill)fill.style.width='3%';if(percent)percent.textContent='3%';
    const started=performance.now();const timerId=setInterval(()=>{if(timer)timer.textContent=((performance.now()-started)/1000).toFixed(1)+'s';},100);
    const reset=()=>{clearInterval(timerId);form.dataset.submitting='0';if(save){save.disabled=false;save.innerHTML=save.dataset.originalText||'Save product';}};
    try{
      const originalFiles=selectedFiles();const compressed=[];
      for(let i=0;i<originalFiles.length;i++){if(state)state.textContent=`Compressing photo ${i+1} of ${originalFiles.length}…`;compressed.push(await compressFile(originalFiles[i]));}
      if(compressed.length>8||compressed.some(file=>file.size>8*1024*1024)){reset();if(state)state.textContent='Choose no more than 8 photos, each under 8 MB.';return;}
      if(state)state.textContent='Uploading photos and saving product…';
      const payload=new FormData(form);payload.delete('images');payload.delete('camera_images');payload.delete('image');
      compressed.forEach(file=>payload.append('images',file,file.name));
      const xhr=new XMLHttpRequest();xhr.open('POST',form.action);xhr.responseType='text';xhr.timeout=120000;
      xhr.upload.onprogress=function(ev){if(ev.lengthComputable){const p=Math.max(3,Math.min(99,Math.round(ev.loaded/ev.total*100)));if(fill)fill.style.width=p+'%';if(percent)percent.textContent=p+'%';if(state)state.textContent=p>=99?'Finishing up…':'Uploading photos & product…';}};
      xhr.onload=function(){clearInterval(timerId);if(timer)timer.textContent=((performance.now()-started)/1000).toFixed(1)+'s';if(xhr.status>=400){reset();if(state)state.textContent=xhr.status===413?'Upload was too large. Try fewer photos or images under 8 MB each.':`Upload failed (HTTP ${xhr.status}). Please retry with JPG, PNG or WebP photos.`;return;}if(fill)fill.style.width='100%';if(percent)percent.textContent='100%';if(state)state.textContent='Upload finished';const target=xhr.responseURL||form.action;if(new URL(target,window.location.href).pathname===new URL(form.action,window.location.href).pathname){document.open();document.write(xhr.responseText);document.close();return;}setTimeout(()=>{window.location.href=target;},300);};
      xhr.onerror=function(){reset();if(state)state.textContent='Upload failed. Check your connection and try again.';};xhr.ontimeout=function(){reset();if(state)state.textContent='The upload timed out. Try fewer photos or a more stable connection.';};xhr.send(payload);
    }catch(err){reset();if(state)state.textContent=err?.message||'Could not prepare photos. Please try again.';}
  });

  const detailRoot=document.querySelector('[data-live-product-id]');
  let detailUpdatedAt='';
  async function refreshProductDetail(){
    if(!detailRoot)return;
    try{
      const id=detailRoot.dataset.liveProductId;const response=await fetch('/api/product/'+encodeURIComponent(id),{cache:'no-store',credentials:'same-origin'});if(!response.ok)return;const data=await response.json();
      if(!data.active){const wrap=detailRoot.querySelector('[data-detail-image-wrap]');if(wrap)wrap.innerHTML='<div class="empty-inline"><strong>This item is no longer available.</strong><p>The store has removed or hidden this listing.</p><a class="text-link" href="/?browse=1">Browse available styles →</a></div>';detailRoot.querySelectorAll('[data-detail-buy-form],[data-detail-rent-form]').forEach(f=>f.hidden=true);return;}
      if(data.updated_at===detailUpdatedAt)return;detailUpdatedAt=data.updated_at;
      document.querySelectorAll('[data-detail-name]').forEach(node=>node.textContent=data.name);
      document.querySelectorAll('[data-detail-category]').forEach(node=>node.textContent=data.category);
      const desc=detailRoot.querySelector('[data-detail-description]');if(desc)desc.textContent=data.description||'A carefully selected style from Shreejee Fashion.';
      const buyAllowed=data.offer==='buy'||data.offer==='both';const rentAllowed=(data.offer==='rent'||data.offer==='both')&&data.rental_price!==null;
      const buyWrap=detailRoot.querySelector('[data-detail-buy-price-wrap]'),rentWrap=detailRoot.querySelector('[data-detail-rent-price-wrap]');if(buyWrap)buyWrap.hidden=!buyAllowed;if(rentWrap)rentWrap.hidden=!rentAllowed;
      const buyForm=detailRoot.querySelector('[data-detail-buy-form]'),rentForm=detailRoot.querySelector('[data-detail-rent-form]');if(buyForm)buyForm.hidden=!buyAllowed;if(rentForm)rentForm.hidden=!rentAllowed;
      const buyPrice='₹'+Number(data.price).toFixed(2),rentPrice='₹'+Number(data.rental_price||0).toFixed(2);const bp=detailRoot.querySelector('[data-detail-buy-price]'),rp=detailRoot.querySelector('[data-detail-rent-price]');if(bp)bp.textContent=buyPrice;if(rp)rp.textContent=rentPrice;
      const bb=detailRoot.querySelector('[data-detail-buy-button]'),rb=detailRoot.querySelector('[data-detail-rent-button]');if(bb)bb.textContent='Buy this item · '+buyPrice;if(rb)rb.textContent='Rent this item · '+rentPrice;
      const image=detailRoot.querySelector('[data-detail-image]'),placeholder=detailRoot.querySelector('[data-detail-placeholder]');if(image&&data.image){image.src='/static/uploads/'+encodeURIComponent(data.image)+'?v='+encodeURIComponent(data.updated_at);image.alt=data.name;image.hidden=false;image.style.display='block';if(placeholder)placeholder.hidden=true;}else if(image){image.removeAttribute('src');image.hidden=true;if(placeholder){placeholder.hidden=false;const label=placeholder.querySelector('span');if(label)label.textContent=data.category;}}
      let gallery=detailRoot.querySelector('.product-gallery-thumbs');if(Array.isArray(data.images)&&data.images.length>1){if(!gallery){gallery=document.createElement('div');gallery.className='product-gallery-thumbs';gallery.setAttribute('aria-label','Other product photos');detailRoot.querySelector('[data-detail-image-wrap]')?.appendChild(gallery);}gallery.replaceChildren(...data.images.map((filename,index)=>{const button=document.createElement('button');button.type='button';button.className='product-gallery-thumb'+(filename===data.image?' active':'');button.dataset.gallerySrc='/static/uploads/'+encodeURIComponent(filename);button.setAttribute('aria-label','View product photo '+(index+1));const thumb=document.createElement('img');thumb.src=button.dataset.gallerySrc;thumb.alt=data.name+' photo '+(index+1);button.appendChild(thumb);button.addEventListener('click',()=>{if(image){image.src=button.dataset.gallerySrc;image.hidden=false;image.alt=data.name;gallery.querySelectorAll('button').forEach(x=>x.classList.toggle('active',x===button));}});return button;}));}else if(gallery){gallery.remove();}
      document.title=data.name+' | '+(document.querySelector('[data-site-key="site_name"]')?.textContent||'Shreejee Fashion');
    }catch(_){/* temporary network loss, retry shortly */}
  }
  if(detailRoot){refreshProductDetail();setInterval(()=>{if(document.visibilityState==='visible')refreshProductDetail();},15000);}

  const categoryFilters=document.getElementById('catalogCategoryFilters');
  async function refreshCategories(){
    if(!categoryFilters)return;
    try{
      const response=await fetch('/api/categories?view='+encodeURIComponent(categoryFilters.dataset.view||'all'),{cache:'no-store',credentials:'same-origin'});if(!response.ok)return;
      const data=await response.json();const fixed=[...categoryFilters.querySelectorAll('[data-filter-kind="all"],[data-filter-kind="buy"],[data-filter-kind="rent"]')];
      const existing=new Map([...categoryFilters.querySelectorAll('[data-filter-kind="category"]')].map(a=>[a.dataset.categoryName,a]));
      const ordered=[];
      (data.categories||[]).forEach(category=>{
        let link=existing.get(category);
        if(!link){link=document.createElement('a');link.dataset.filterKind='category';link.dataset.categoryName=category;}
        link.dataset.categoryName=category;link.textContent=category;
        const url=new URL('/',window.location.origin);
        const current=new URL(window.location.href);const view=current.searchParams.get('view');const query=current.searchParams.get('q');
        const selected=current.searchParams.get('category')===category;
        // Clicking the already-selected category clears it (back to all styles).
        if(selected)url.searchParams.set('browse','1');else url.searchParams.set('category',category);
        if(view&&view!=='all')url.searchParams.set('view',view);if(query)url.searchParams.set('q',query);
        link.classList.toggle('active',selected);if(selected){link.setAttribute('aria-pressed','true');link.title='Click again to clear this filter';}else{link.removeAttribute('aria-pressed');link.removeAttribute('title');}
        link.href=url.pathname+url.search;ordered.push(link);
      });
      categoryFilters.replaceChildren(...fixed,...ordered);
    }catch(_){/* next poll retries */}
  }
  refreshCategories();setInterval(()=>{if(document.visibilityState==='visible')refreshCategories();},60000);
})();

// ---- v1.0.4: appearance preferences page and mobile-friendly Fresh Pop taskbar ----
(function(){
  function applyMenuPosition(pos){if(!['tl','tr','bl','br'].includes(pos))pos=document.body.dataset.menuDefault||'tl';document.body.dataset.menuPos=pos;document.body.dataset.menuSide=pos.endsWith('r')?'right':'left';document.querySelectorAll('[data-menu-location]').forEach(s=>s.value=pos);}
  try{applyMenuPosition(localStorage.getItem('sf_menu_location')||document.body.dataset.menuDefault||'tl');}catch(_){applyMenuPosition(document.body.dataset.menuDefault||'tl');}
  document.addEventListener('change',function(event){const select=event.target.closest('[data-menu-location]');if(!select)return;applyMenuPosition(select.value);if(!document.querySelector('[data-appearance-page]')){try{localStorage.setItem('sf_menu_location',select.value);}catch(_){}}});
  document.addEventListener('click',function(event){const look=event.target.closest('[data-ui-open]');if(look){event.preventDefault();window.location.href='/appearance';}});
  // Hide on every downward scroll increment and restore on any upward movement; always show at top.
  // Bind on all pages, not just when Fresh Pop was active at first paint, so appearance-page live preview works too.
  let lastScrollY=window.scrollY||window.pageYOffset||0;
  let scheduled=false;
  const updateDock=()=>{
    const current=window.scrollY||window.pageYOffset||0;
    if(document.body.dataset.ui!=='fresh'){document.body.classList.remove('fresh-dock-hidden');}
    else if(current<=24){document.body.classList.remove('fresh-dock-hidden');}
    else if(current>lastScrollY+.25){document.body.classList.add('fresh-dock-hidden');}
    else if(current<lastScrollY-.25){document.body.classList.remove('fresh-dock-hidden');}
    lastScrollY=current;scheduled=false;
  };
  window.addEventListener('scroll',()=>{if(scheduled)return;scheduled=true;window.requestAnimationFrame(updateDock);},{passive:true});
  window.addEventListener('pageshow',()=>{lastScrollY=window.scrollY||0;if(lastScrollY<=24)document.body.classList.remove('fresh-dock-hidden');});
  // On touch browsers, follow the finger direction too; this helps when mobile scroll events are coalesced.
  let lastTouchY=null;
  document.addEventListener('touchstart',e=>{if(e.touches&&e.touches.length===1)lastTouchY=e.touches[0].clientY;},{passive:true});
  document.addEventListener('touchmove',e=>{
    if(document.body.dataset.ui!=='fresh'||!e.touches||e.touches.length!==1||lastTouchY===null)return;
    const y=e.touches[0].clientY, delta=y-lastTouchY;
    if(Math.abs(delta)>=1.25){if(delta<0)document.body.classList.add('fresh-dock-hidden');else document.body.classList.remove('fresh-dock-hidden');lastTouchY=y;}
  },{passive:true});
  document.addEventListener('touchend',()=>{lastTouchY=null;},{passive:true});
  window.addEventListener('keydown',e=>{if(['ArrowUp','PageUp','Home'].includes(e.key)&&document.body.dataset.ui==='fresh')document.body.classList.remove('fresh-dock-hidden');});
})();

// Password visibility button for every password field (login, registration, reset forms).
(function(){
  const eyeSvg='<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7-10-7-10-7Z"/><circle cx="12" cy="12" r="3"/></svg>';
  const eyeOffSvg='<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 3l18 18"/><path d="M10.6 10.6a2 2 0 0 0 2.8 2.8"/><path d="M9.9 5.2A10.7 10.7 0 0 1 12 5c6.4 0 10 7 10 7a15 15 0 0 1-3.1 3.7M6.2 6.2C3.5 8.1 2 12 2 12s3.6 7 10 7c1.1 0 2.1-.2 3-.5"/></svg>';
  function enhancePassword(input){
    if(!input || input.dataset.passwordEyeReady==='1' || input.type!=='password')return;
    input.dataset.passwordEyeReady='1';
    const parent=input.parentNode;
    if(!parent)return;
    const wrapper=document.createElement('span');wrapper.className='password-eye-wrap';
    parent.insertBefore(wrapper,input);wrapper.appendChild(input);
    input.classList.add('password-eye-input');
    const toggle=document.createElement('button');toggle.type='button';toggle.className='password-eye-toggle';toggle.setAttribute('aria-label','Show password');toggle.setAttribute('aria-pressed','false');toggle.title='Show password';toggle.innerHTML=eyeSvg;
    toggle.addEventListener('click',function(){
      const show=input.type==='password';input.type=show?'text':'password';
      toggle.setAttribute('aria-label',show?'Hide password':'Show password');toggle.setAttribute('aria-pressed',show?'true':'false');toggle.title=show?'Hide password':'Show password';toggle.innerHTML=show?eyeOffSvg:eyeSvg;
      try{input.focus({preventScroll:true});}catch(_){input.focus();}
    });
    wrapper.appendChild(toggle);
  }
  document.querySelectorAll('input[type="password"]').forEach(enhancePassword);
  // Also support password inputs inserted by future form fragments.
  const observer=new MutationObserver(records=>records.forEach(record=>record.addedNodes.forEach(node=>{
    if(node.nodeType!==1)return;
    if(node.matches?.('input[type="password"]'))enhancePassword(node);
    node.querySelectorAll?.('input[type="password"]').forEach(enhancePassword);
  })));
  observer.observe(document.body,{childList:true,subtree:true});
})();

/* v1.0.11 — success/error messages, mobile tables, form validation feedback */
(function(){
  function dismiss(el){if(!el||el.classList.contains('is-leaving'))return;el.classList.add('is-leaving');setTimeout(()=>{const stack=el.parentElement;el.remove();if(stack&&!stack.children.length)stack.remove();},320);}
  function arm(el){
    const bad=el.classList.contains('flash-danger')||el.classList.contains('flash-warning');
    const t=setTimeout(()=>dismiss(el),bad?8000:4500);
    el.addEventListener('mouseenter',()=>clearTimeout(t));
    el.querySelector('.flash-close')?.addEventListener('click',()=>{clearTimeout(t);dismiss(el);});
  }
  document.querySelectorAll('.flash-stack .flash').forEach(arm);
  // Public helper: sfNotify('Saved!', 'success'|'danger'|'warning'|'info')
  window.sfNotify=function(message,type){
    type=type||'info';
    let stack=document.querySelector('.flash-stack');
    if(!stack){stack=document.createElement('div');stack.className='flash-stack';stack.setAttribute('role','region');document.body.appendChild(stack);}
    const el=document.createElement('div');el.className='flash flash-'+type;el.setAttribute('role',type==='danger'||type==='warning'?'alert':'status');
    const ic=document.createElement('span');ic.className='flash-icon';ic.setAttribute('aria-hidden','true');ic.textContent={success:'✓',danger:'!',warning:'▲'}[type]||'i';
    const tx=document.createElement('span');tx.className='flash-text';tx.textContent=message;
    const cl=document.createElement('button');cl.type='button';cl.className='flash-close';cl.setAttribute('aria-label','Dismiss');cl.textContent='×';
    el.append(ic,tx,cl);stack.appendChild(el);arm(el);
  };
  // Browser form validation → visible error message instead of a silent tooltip
  let lastInvalid=0;
  document.addEventListener('invalid',function(e){
    const f=e.target;if(!f||!f.classList)return;f.classList.add('sf-field-error');
    if(Date.now()-lastInvalid>600){lastInvalid=Date.now();
      const label=(f.labels&&f.labels[0]?f.labels[0].childNodes[0]?.textContent:'')||f.getAttribute('placeholder')||f.name||'This field';
      window.sfNotify((label.trim()||'This field')+': '+(f.validationMessage||'please check this field.'),'danger');}
  },true);
  document.addEventListener('input',e=>e.target.classList&&e.target.classList.remove('sf-field-error'));
  // Network failure on async actions
  window.addEventListener('offline',()=>window.sfNotify('You are offline. Check your connection and try again.','danger'));
  window.addEventListener('online',()=>window.sfNotify('Back online.','success'));

  // Label table cells from their column headers so tables can stack on phones
  document.querySelectorAll('table').forEach(table=>{
    const heads=[...table.querySelectorAll('thead th')].map(th=>th.textContent.trim());
    if(!heads.length)return;
    table.querySelectorAll('tbody tr').forEach(tr=>{
      [...tr.children].forEach((td,i)=>{if(td.tagName==='TD'&&!td.hasAttribute('data-label')){td.setAttribute('data-label',heads[i]||'');if(!td.hasAttribute('colspan')&&!td.querySelector(':scope>.td-val')){const w=document.createElement('div');w.className='td-val';while(td.firstChild)w.appendChild(td.firstChild);td.appendChild(w);}}});
    });
  });
})();

// ---- v1.0.12: delivery PIN checker
(function(){
  document.querySelectorAll('[data-pin-check]').forEach(box=>{
    const input=box.querySelector('input'),btn=box.querySelector('[data-pin-btn]'),out=box.querySelector('[data-pin-result]');
    async function run(){
      const pin=(input.value||'').replace(/\D/g,'');
      out.className='small pin-check-result';
      if(pin.length!==6){out.textContent='Enter a valid 6-digit PIN code.';out.classList.add('pin-no');return;}
      out.textContent='Checking…';
      try{const r=await fetch(box.dataset.url+'?pin='+encodeURIComponent(pin),{credentials:'same-origin'});const d=await r.json();out.textContent=d.message;out.classList.add(d.state==='yes'?'pin-yes':(d.state==='unknown'?'pin-maybe':'pin-no'));}
      catch(e){out.textContent='Could not check right now. Please try again.';out.classList.add('pin-no');}
    }
    btn?.addEventListener('click',run);
    input?.addEventListener('keydown',e=>{if(e.key==='Enter'){e.preventDefault();run();}});
    if(input&&input.value.length===6)run();
  });
})();

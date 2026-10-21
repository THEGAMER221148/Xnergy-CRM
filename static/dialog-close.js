(() => {
  const style = document.createElement('style');
  style.textContent = 'dialog .dialog-close-bar{position:sticky;top:0;z-index:100;background:#fff;padding:8px 0 12px;margin:0 0 8px;display:flex;justify-content:flex-start}dialog .dialog-close-bar button{min-height:40px;cursor:pointer}';
  document.head.append(style);
  function install(dialog) {
    if(dialog.querySelector(':scope > .dialog-close-bar')) return;
    const bar = document.createElement('div');
    bar.className = 'dialog-close-bar';
    const button = document.createElement('button');
    button.type = 'button'; button.className = 'btn';
    button.textContent = '× Close'; button.setAttribute('aria-label','Close window');
    button.addEventListener('click', () => dialog.close());
    bar.append(button); dialog.prepend(bar);
  }
  const scan = () => document.querySelectorAll('dialog').forEach(install);
  new MutationObserver(scan).observe(document.body,{childList:true,subtree:true});
  scan();
})();

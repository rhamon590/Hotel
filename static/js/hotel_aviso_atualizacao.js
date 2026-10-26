(function () {
  'use strict';
  const dialog = document.getElementById('hotel-update-dialog');
  const next = document.getElementById('ha-continue');
  if (!dialog || !next) return;
  if (typeof dialog.showModal === 'function') {
    dialog.removeAttribute('open');
    dialog.showModal();
    dialog.addEventListener('cancel', function (event) { event.preventDefault(); });
  }
  next.focus({preventScroll:true});
  next.addEventListener('click', function (event) {
    if (event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
    next.setAttribute('aria-busy', 'true');
    next.querySelector('[data-ha-label]').textContent = 'Abrindo alteração de senha…';
  });
  window.addEventListener('pageshow', function () {
    next.removeAttribute('aria-busy');
    next.querySelector('[data-ha-label]').textContent = 'Entendi e vou alterar minha senha';
  });
}());

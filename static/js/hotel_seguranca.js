(function () {
    'use strict';
    function token() { return document.querySelector('meta[name="hotel-csrf"]')?.content || ''; }
    function protect(form) {
        const method = (form.method || 'get').toUpperCase();
        if (!['POST', 'PUT', 'PATCH', 'DELETE'].includes(method)) return;
        const target = new URL(form.action || location.href, location.href);
        if (target.origin !== location.origin) return;
        let field = form.querySelector('input[name="hotel_csrf"]');
        if (!field) { field = document.createElement('input'); field.type = 'hidden'; field.name = 'hotel_csrf'; form.append(field); }
        field.value = token();
    }
    document.addEventListener('submit', function (event) {
        if (event.target instanceof HTMLFormElement) protect(event.target);
    }, true);
    document.addEventListener('DOMContentLoaded', function () { document.querySelectorAll('form').forEach(protect); });
    // Mantém os fetch existentes, acrescentando o token apenas em escritas na mesma origem.
    const originalFetch = window.fetch;
    if (originalFetch) window.fetch = function (input, options) {
        const opts = Object.assign({}, options || {});
        const req = input instanceof Request ? input : null;
        const method = (opts.method || req?.method || 'GET').toUpperCase();
        const url = new URL(req ? req.url : input, location.href);
        if (url.origin === location.origin && ['POST','PUT','PATCH','DELETE'].includes(method)) {
            const headers = new Headers(opts.headers || req?.headers);
            headers.set('X-CSRFToken', token());
            opts.headers = headers;
        }
        return originalFetch.call(this, input, opts);
    };
}());

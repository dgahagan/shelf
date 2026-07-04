// Read the csrf_token cookie. Used by HTMX hook below and by raw fetch() callers.
window.csrfToken = function() {
    const match = document.cookie.split('; ').find(r => r.startsWith('csrf_token='));
    return match ? decodeURIComponent(match.split('=')[1]) : '';
};
// Attach CSRF token to every HTMX state-mutating request (double-submit cookie)
document.addEventListener('htmx:configRequest', function(evt) {
    if (['POST', 'PUT', 'PATCH', 'DELETE'].includes(evt.detail.verb.toUpperCase())) {
        const match = document.cookie.split('; ').find(r => r.startsWith('csrf_token='));
        if (match) {
            evt.detail.headers['X-CSRF-Token'] = match.split('=')[1];
        }
    }
});
// Inject _csrf hidden field into plain HTML POST forms (non-HTMX)
document.addEventListener('DOMContentLoaded', function() {
    const csrfCookie = document.cookie.split('; ').find(r => r.startsWith('csrf_token='));
    if (csrfCookie) {
        const csrfValue = decodeURIComponent(csrfCookie.split('=')[1]);
        document.querySelectorAll('form[method="post"], form[method="POST"]').forEach(function(form) {
            if (!form.querySelector('input[name="_csrf"]')) {
                var input = document.createElement('input');
                input.type = 'hidden'; input.name = '_csrf'; input.value = csrfValue;
                form.appendChild(input);
            }
        });
    }
});

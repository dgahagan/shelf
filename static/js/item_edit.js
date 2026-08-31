function coverDrop() {
    return {
        dragging: false,
        preview: false,
        handleDrop(e) {
            this.dragging = false;
            var file = e.dataTransfer.files[0];
            if (file && file.type.startsWith('image/')) {
                var dt = new DataTransfer();
                dt.items.add(file);
                this.$refs.coverInput.files = dt.files;
                this.preview = URL.createObjectURL(file);
            }
        },
        handleFile(e) {
            var file = e.target.files[0];
            if (file) this.preview = URL.createObjectURL(file);
        }
    }
}

// Existing libraries can contain ISBN-shaped identifiers written by older
// Shelf versions before checksum validation existed. Do not resubmit an
// unchanged ISBN when saving an unrelated edit: the server only needs to
// validate the identifier when the user actually changes it. Disabling the
// unchanged field at submit time keeps legacy rows editable without weakening
// validation for new values (disabled controls are not included in form data).
document.addEventListener('DOMContentLoaded', function () {
    var isbn = document.getElementById('isbn');
    if (!isbn || !isbn.form) return;
    isbn.form.addEventListener('submit', function () {
        if (isbn.value === isbn.defaultValue) isbn.disabled = true;
    });
});

// CSP build has no global fallback — register so x-data="coverDrop" resolves.
document.addEventListener('alpine:init', function () {
    Alpine.data('coverDrop', coverDrop);
});

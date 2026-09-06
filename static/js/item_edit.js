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
    };
}

function updateEditSectionVisibility(root) {
    var mediaSelect = root.querySelector('#media_type');
    if (!mediaSelect) return;
    var mediaType = mediaSelect.value;

    root.querySelectorAll('[data-media-types]').forEach(function (element) {
        var supported = (element.dataset.mediaTypes || '').split(/\s+/).filter(Boolean);
        var alwaysVisible = element.dataset.alwaysVisible === 'true';
        element.hidden = !alwaysVisible && supported.indexOf(mediaType) === -1;
    });
}

document.addEventListener('DOMContentLoaded', function () {
    var root = document.querySelector('[data-item-edit-sections]');
    if (!root) return;
    var mediaSelect = root.querySelector('#media_type');
    updateEditSectionVisibility(root);
    if (mediaSelect) {
        mediaSelect.addEventListener('change', function () {
            updateEditSectionVisibility(root);
        });
    }
});

// CSP build has no global fallback — register so x-data="coverDrop" resolves.
document.addEventListener('alpine:init', function () {
    Alpine.data('coverDrop', coverDrop);
});

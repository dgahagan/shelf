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

// CSP build has no global fallback — register so x-data="coverDrop" resolves.
document.addEventListener('alpine:init', function () {
    Alpine.data('coverDrop', coverDrop);
});

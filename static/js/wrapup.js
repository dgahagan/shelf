// Wrap-up image: draws the period's covers and headline numbers onto a
// <canvas> and offers the result as a PNG download (and, where the browser can
// share files, a Share button). Everything it draws comes from the data-*
// attributes stats_wrapup.html renders; it makes no request of its own beyond
// the bundled font and the same-origin /covers/ files, so the canvas is never
// tainted and toBlob cannot throw. Nothing is uploaded or stored.
(function () {
    'use strict';

    var W = 1080;
    var H = 1350;
    var PAD = 72;
    var GAP = 16;
    var FAMILY = 'Inter, system-ui, sans-serif';
    var COLOR = {
        bg: '#0f1117', card: '#1a1d27', hover: '#242836', accent: '#6366f1',
        accent2: '#818cf8', text: '#e2e8f0', muted: '#94a3b8', border: '#2d3148',
    };

    function loadFonts() {
        if (typeof FontFace === 'undefined' || !document.fonts) return Promise.resolve(false);
        var faces = [
            new FontFace('Inter', 'url(/static/vendor/inter-4.1-regular.woff2)', { weight: '400' }),
            new FontFace('Inter', 'url(/static/vendor/inter-4.1-bold.woff2)', { weight: '700' }),
        ];
        return Promise.all(faces.map(function (f) { return f.load(); })).then(function (loaded) {
            loaded.forEach(function (f) { document.fonts.add(f); });
            return true;
        }).catch(function () {
            // The image still draws, in the fallback stack, rather than not at all.
            return false;
        });
    }

    // Resolves with the loaded image, or null for a missing, foreign or broken
    // cover — one bad cover becomes a placeholder tile, never a failed export.
    function loadCover(src) {
        return new Promise(function (resolve) {
            if (!src) { resolve(null); return; }
            var url;
            try { url = new URL(src, window.location.href); } catch (e) { resolve(null); return; }
            if (url.origin !== window.location.origin) { resolve(null); return; }
            var img = new Image();
            img.onload = function () { resolve(img.naturalWidth ? img : null); };
            img.onerror = function () { resolve(null); };
            img.src = url.href;
        });
    }

    function font(weight, size) {
        return weight + ' ' + size + 'px ' + FAMILY;
    }

    function ellipsize(ctx, text, maxWidth) {
        if (ctx.measureText(text).width <= maxWidth) return text;
        var s = text;
        while (s.length > 1 && ctx.measureText(s + '…').width > maxWidth) s = s.slice(0, -1);
        return s.replace(/\s+$/, '') + '…';
    }

    // Word-wraps into at most maxLines; the last kept line carries an ellipsis
    // when text was dropped, and any line too wide for the tile is ellipsised.
    function wrapLines(ctx, text, maxWidth, maxLines) {
        var words = text.split(/\s+/).filter(Boolean);
        var lines = [];
        var line = '';
        words.forEach(function (word) {
            var next = line ? line + ' ' + word : word;
            if (line && ctx.measureText(next).width > maxWidth) {
                lines.push(line);
                line = word;
            } else {
                line = next;
            }
        });
        if (line) lines.push(line);
        var truncated = lines.length > maxLines;
        lines = lines.slice(0, maxLines);
        return lines.map(function (l, i) {
            var last = i === lines.length - 1;
            return ellipsize(ctx, truncated && last ? l + '\u2026' : l, maxWidth);
        });
    }

    function roundRect(ctx, x, y, w, h, r) {
        ctx.beginPath();
        if (ctx.roundRect) {
            ctx.roundRect(x, y, w, h, r);
        } else {
            ctx.rect(x, y, w, h);
        }
    }

    // Picks the column count that gives the largest tile of the given
    // height:width ratio inside the box.
    function layoutGrid(n, boxW, boxH, ratio) {
        var best = null;
        for (var cols = 1; cols <= 6; cols++) {
            var rows = Math.ceil(n / cols);
            var cellW = (boxW - GAP * (cols - 1)) / cols;
            var cellH = (boxH - GAP * (rows - 1)) / rows;
            var tileW = Math.min(cellW, cellH / ratio);
            if (!best || tileW > best.tileW) best = { cols: cols, rows: rows, tileW: tileW };
        }
        return best;
    }

    function drawTile(ctx, tile, img, x, y, w, h) {
        roundRect(ctx, x, y, w, h, 8);
        ctx.fillStyle = img ? COLOR.card : COLOR.hover;
        ctx.fill();
        ctx.save();
        roundRect(ctx, x, y, w, h, 8);
        ctx.clip();
        if (img) {
            // Contain, never stretch: portrait and square covers both fit.
            var scale = Math.min(w / img.naturalWidth, h / img.naturalHeight);
            var dw = img.naturalWidth * scale;
            var dh = img.naturalHeight * scale;
            ctx.drawImage(img, x + (w - dw) / 2, y + (h - dh) / 2, dw, dh);
        } else {
            var size = Math.max(12, Math.round(w / 9));
            ctx.font = font(700, size);
            ctx.fillStyle = COLOR.muted;
            ctx.textAlign = 'center';
            ctx.textBaseline = 'middle';
            var maxLines = Math.max(1, Math.floor((h - size) / (size * 1.25)));
            var lines = wrapLines(ctx, tile.title || t('Untitled'), w - size * 1.5, Math.min(maxLines, 6));
            var lineH = size * 1.25;
            var top = y + h / 2 - (lines.length - 1) * lineH / 2;
            lines.forEach(function (l, i) { ctx.fillText(l, x + w / 2, top + i * lineH); });
        }
        ctx.restore();
    }

    // 2:3 tiles, unless most of the loaded covers are square (audiobooks,
    // music) — then square tiles, so each cover is not banded by empty card.
    function tileRatio(images) {
        var loaded = images.filter(Boolean);
        var square = loaded.filter(function (img) {
            return img.naturalHeight / img.naturalWidth < 1.2;
        }).length;
        return loaded.length && square * 2 > loaded.length ? 1 : 1.5;
    }

    function draw(canvas, data, tiles, images) {
        var ctx = canvas.getContext('2d');
        ctx.fillStyle = COLOR.bg;
        ctx.fillRect(0, 0, W, H);
        ctx.textAlign = 'left';
        ctx.textBaseline = 'alphabetic';
        var inner = W - PAD * 2;

        ctx.fillStyle = COLOR.accent2;
        ctx.font = font(700, 28);
        ctx.fillText('WRAP-UP', PAD, PAD + 28);

        ctx.fillStyle = COLOR.text;
        ctx.font = font(700, 76);
        ctx.fillText(ellipsize(ctx, data.label, inner), PAD, PAD + 28 + 90);

        var y = PAD + 28 + 90 + 70;
        ctx.font = font(700, 44);
        var finished = data.finished.map(function (f) { return f.count + ' ' + f.word; }).join('  ·  ');
        ctx.fillStyle = COLOR.text;
        ctx.fillText(ellipsize(ctx, finished || 'Nothing finished', inner), PAD, y);

        y += 52;
        ctx.font = font(400, 32);
        ctx.fillStyle = COLOR.muted;
        var added = data.added + ' added to the library';
        ctx.fillText(ellipsize(ctx, added, inner), PAD, y);
        if (data.topAuthor) {
            y += 44;
            ctx.fillText(ellipsize(ctx, 'Top author: ' + data.topAuthor + ' (' + data.topAuthorCount + ')', inner), PAD, y);
        }

        var gridTop = y + 48;
        var gridBottom = H - PAD - 64;
        var boxH = gridBottom - gridTop;
        if (tiles.length) {
            var ratio = tileRatio(images);
            var g = layoutGrid(tiles.length, inner, boxH, ratio);
            var tileW = g.tileW;
            var tileH = tileW * ratio;
            var gridW = g.cols * tileW + (g.cols - 1) * GAP;
            var gridH = g.rows * tileH + (g.rows - 1) * GAP;
            var x0 = PAD + (inner - gridW) / 2;
            var y0 = gridTop + (boxH - gridH) / 2;
            tiles.forEach(function (tile, i) {
                var col = i % g.cols;
                var row = Math.floor(i / g.cols);
                drawTile(ctx, tile, images[i], x0 + col * (tileW + GAP), y0 + row * (tileH + GAP), tileW, tileH);
            });
        }

        ctx.textBaseline = 'alphabetic';
        ctx.textAlign = 'left';
        ctx.fillStyle = COLOR.accent2;
        ctx.font = font(700, 40);
        ctx.fillText('Shelf', PAD, H - PAD);
        ctx.textAlign = 'right';
        ctx.fillStyle = COLOR.muted;
        ctx.font = font(400, 26);
        var caption = (data.showing === 'finished' ? t('Finished in {period}') : t('Added in {period}')).replace('{period}', data.label);
        if (data.tileCount > tiles.length) caption = t('Latest {shown} of {total}').replace('{shown}', tiles.length).replace('{total}', data.tileCount) + ' · ' + caption;
        ctx.fillText(ellipsize(ctx, caption, inner - 200), W - PAD, H - PAD);
    }

    function readData(root) {
        var finished = Array.prototype.map.call(root.querySelectorAll('[data-wrapup-finished]'), function (el) {
            return { count: el.getAttribute('data-count'), word: el.getAttribute('data-word') };
        });
        return {
            period: root.getAttribute('data-period'),
            label: root.getAttribute('data-label') || '',
            showing: root.getAttribute('data-showing'),
            added: root.getAttribute('data-added') || '0',
            topAuthor: root.getAttribute('data-top-author'),
            topAuthorCount: root.getAttribute('data-top-author-count'),
            tileCount: parseInt(root.getAttribute('data-tile-total') || '0', 10),
            finished: finished,
        };
    }

    function init() {
        var root = document.getElementById('wrapup-data');
        var canvas = document.getElementById('wrapup-canvas');
        if (!root || !canvas) return;
        var status = document.getElementById('wrapup-status');
        var download = document.getElementById('wrapup-download');
        var share = document.getElementById('wrapup-share');
        var data = readData(root);
        var tiles = Array.prototype.map.call(document.querySelectorAll('[data-wrapup-tile]'), function (el) {
            return { cover: el.getAttribute('data-cover'), title: el.getAttribute('data-title') };
        });
        var filename = 'shelf-wrapup-' + data.period + '.png';
        var objectUrl = null;
        var file = null;

        canvas.width = W;
        canvas.height = H;

        Promise.all([loadFonts(), Promise.all(tiles.map(function (t) { return loadCover(t.cover); }))])
            .then(function (results) {
                canvas.setAttribute('data-font', results[0] ? 'inter' : 'fallback');
                draw(canvas, data, tiles, results[1]);
                canvas.toBlob(function (blob) {
                    if (!blob) {
                        if (status) status.textContent = t('This browser could not export the image.');
                        return;
                    }
                    if (objectUrl) URL.revokeObjectURL(objectUrl);
                    objectUrl = URL.createObjectURL(blob);
                    download.href = objectUrl;
                    download.setAttribute('download', filename);
                    download.classList.remove('hidden');
                    try {
                        file = new File([blob], filename, { type: 'image/png' });
                        if (share && navigator.canShare && navigator.canShare({ files: [file] })) {
                            share.classList.remove('hidden');
                        }
                    } catch (e) {
                        file = null;
                    }
                    if (status) status.textContent = '';
                    canvas.setAttribute('data-ready', 'true');
                }, 'image/png');
            });

        if (share) {
            share.addEventListener('click', function () {
                if (!file) return;
                navigator.share({ files: [file], title: data.label + ' wrap-up' }).catch(function () {
                    // Cancelled or refused by the device; the download link still works.
                });
            });
        }
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();

// Framework-free webcam capture module, consumed by the intake page's
// viewfinder (desktop browsers where the HTML `capture` attribute is
// unsupported). Mirrors scanner-engine.js: this module owns getUserMedia
// acquisition/teardown and frame grabbing only — UI, toasts and page state
// stay with the caller.
//
// Contract:
//   window.createPhotoCapture({ videoEl })
//     -> { start(), stop(), grab(), active() }
//
//   start() returns a Promise that rejects on acquisition failure (the
//   caller handles the error UX); a start() re-entered while one is already
//   in flight returns the SAME promise rather than acquiring a second
//   stream. stop() never rejects and is a safe no-op when idle. grab()
//   rejects if there is no live stream. Never touches page state, never
//   raises toasts.
(function () {
    'use strict';

    function resolveEl(ref) {
        return typeof ref === 'string' ? document.getElementById(ref) : ref;
    }

    window.createPhotoCapture = function (opts) {
        var video = resolveEl(opts.videoEl);
        var stream = false;
        var starting = false;

        function stopStream(s) {
            if (!s) return;
            var tracks = s.getTracks();
            for (var i = 0; i < tracks.length; i++) {
                try { tracks[i].stop(); } catch (e) {}
            }
        }

        function stop() {
            stopStream(stream);
            stream = false;
            video.srcObject = null;
            return Promise.resolve();
        }

        function start() {
            if (starting) return starting;

            starting = stop().then(function () {
                return navigator.mediaDevices.getUserMedia({
                    audio: false,
                    video: {
                        facingMode: { ideal: 'environment' },
                        width: { ideal: 4096 },
                        height: { ideal: 2160 }
                    }
                });
            }).then(function (newStream) {
                stream = newStream;
                video.srcObject = newStream;
                return video.play();
            }).catch(function (err) {
                var failed = stream;
                stream = false;
                video.srcObject = null;
                stopStream(failed);
                throw err;
            }).then(function () {
                starting = false;
            }, function (err) {
                starting = false;
                throw err;
            });

            return starting;
        }

        function grab() {
            if (!stream || video.videoWidth === 0) {
                return Promise.reject(new Error('No live camera stream to grab a frame from.'));
            }
            var canvas = document.createElement('canvas');
            canvas.width = video.videoWidth;
            canvas.height = video.videoHeight;
            var ctx = canvas.getContext('2d');
            ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
            return new Promise(function (resolve, reject) {
                canvas.toBlob(function (blob) {
                    if (blob) resolve(blob);
                    else reject(new Error('Failed to encode captured frame as JPEG.'));
                }, 'image/jpeg', 0.92);
            });
        }

        function active() {
            return !!stream;
        }

        return {
            start: start,
            stop: stop,
            grab: grab,
            active: active
        };
    };
})();

function pollStatus() {
    fetch('/twitch/now_playing')
        .then(res => res.json())
        .then(data => {
            const nowDiv = document.getElementById('now_playing_twitch');
            const adDiv = document.getElementById('adbreak');

            if (data.status === 'playing') {
                nowDiv.classList.remove('d-none');
                nowDiv.classList.add('d-flex');
                document.getElementById('np_name').textContent = data.streamer;
                document.getElementById('np_status').textContent = 'playing';
            } else {
                nowDiv.classList.add('d-none');
                nowDiv.classList.remove('d-flex');
            }

            if (data.ad_break) {
                if (adDiv) {
                    adDiv.style.display = 'block';
                    adDiv.textContent = data.ad_break;
                }
            } else {
                if (adDiv) {
                    adDiv.style.display = 'none';
                }
            }
        });
}

function stopStream() {
    fetch('/twitch/stop', { method: 'POST' })
        .then(res => res.json())
        .then(data => {
            // Instantly evaluate status changes to clear the UI element clean out
            pollStatus();
        });
}

// Map the worker function so that inline HTML hooks wrapper execution safely
function _basePlayStream(rowid, streamer) {
    const startingIndicator = document.getElementById('stream_starting');
    if (startingIndicator) startingIndicator.style.display = 'block';

    fetch('/twitch/play/' + rowid, { method: 'POST' })
        .then(res => res.json())
        .then(data => {
            if (data.status === 'playing' || data.status === 'already_playing') {
                waitUntilStreamStarts(() => {
                    if (startingIndicator) startingIndicator.style.display = 'none';
                    pollStatus();
                });
            }
        });
}

function waitUntilStreamStarts(callback, attempts = 40) {
    // Twitch stream resolution (especially with ad-handling) can take 15-20s.
    // Poll every 750ms for up to 40 attempts (30 seconds total).
    if (attempts <= 0) {
        // Timed out — hide the "starting" indicator and give up gracefully
        const startingIndicator = document.getElementById('stream_starting');
        if (startingIndicator) startingIndicator.style.display = 'none';
        return;
    }

    fetch('/twitch/now_playing')
        .then(res => res.json())
        .then(data => {
            if (data.status === 'playing') {
                callback();
            } else {
                setTimeout(() => waitUntilStreamStarts(callback, attempts - 1), 750);
            }
        });
}

document.addEventListener('DOMContentLoaded', () => {
    pollStatus();
    setInterval(pollStatus, 5000);
});

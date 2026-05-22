function pollStatus() {
    fetch('/twitch/now_playing')
        .then(res => res.json())
        .then(data => {
            const nowDiv = document.getElementById('now_playing_twitch');
            const adDiv = document.getElementById('adbreak');

            if (data.status === 'playing') {
                // Remove hidden utilities and enforce display rules explicitly
                nowDiv.classList.remove('d-none');
                nowDiv.style.setProperty('display', 'flex', 'important');
                
                document.getElementById('np_name').textContent = data.streamer;
                document.getElementById('np_status').textContent = '(Live)';
            } else {
                nowDiv.style.setProperty('display', 'none', 'important');
                nowDiv.classList.add('d-none');
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
            pollStatus();
        });
}

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
    if (attempts <= 0) {
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

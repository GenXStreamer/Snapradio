function updateRadioTrackTitle() {
    const trackElement = document.getElementById('liveTrackTitle');
    const logoElement = document.getElementById('nowPlayingLogo');
    
    // If the user isn't playing anything (no player card on screen), stop checking
    if (!trackElement) return;

    fetch('/radio/track_title')
        .then(response => response.json())
        .then(data => {
            if (data.is_icy && data.track_title && data.track_title.trim() !== "") {
                // Update the song title dynamically
                trackElement.textContent = "Now Playing: " + data.track_title;
                
                // If a track artwork URL was found by Deezer/iTunes, update the logo!
                if (data.track_image && data.track_image !== "") {
                    logoElement.src = data.track_image;
                }
            } else {
                // Keep the loading message until the background thread finds a title
                if (trackElement.textContent.includes("Now Playing:")) {
                     trackElement.textContent = "🔊 Audio Stream Connected";
                }
            }
        })
        .catch(err => console.error("Error updating player visuals:", err));
}

// Poll the backend every 5 seconds instead of 10 for snappier startup responses
setInterval(updateRadioTrackTitle, 5000);

// Run immediately on page load
document.addEventListener("DOMContentLoaded", updateRadioTrackTitle);

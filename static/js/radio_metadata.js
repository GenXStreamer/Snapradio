/**
 * radio_metadata.js
 * Asynchronously polls the Flask server for track metadata,
 * toggling container row elements depending on ICY validity.
 */
(function () {
    function pollRadioMetadata() {
        const trackTitle = document.getElementById("liveTrackTitle");
        const trackContainer = document.getElementById("liveTrackContainer");
        
        if (!trackTitle || !trackContainer) {
            return; 
        }

        fetch('/radio/track_title')
            .then(response => {
                if (!response.ok) throw new Error("Metadata network error");
                return response.json();
            })
            .then(data => {
                if (data.is_icy && data.track_title && data.track_title.trim() !== "") {
                    // Reveal the container and push text updates
                    trackContainer.style.display = "block";
                    if (trackTitle.textContent !== data.track_title) {
                        trackTitle.textContent = data.track_title;
                    }
                } else {
                    // Hide if it's a standard stream link
                    trackContainer.style.display = "none";
                    trackTitle.textContent = "";
                }
            })
            .catch(error => {
                console.error("Error updating stream track context:", error);
            });
    }

    document.addEventListener("DOMContentLoaded", function () {
        if (document.getElementById("liveTrackTitle")) {
            pollRadioMetadata();
            setInterval(pollRadioMetadata, 5000); // Check runtime updates every 5 seconds
        }
    });
})();

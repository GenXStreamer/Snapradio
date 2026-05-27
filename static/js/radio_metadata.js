
//function updateRadioTrackTitle() {
 //   const trackElement = document.getElementById('liveTrackTitle');
  //  const containerElement = document.getElementById('liveTrackContainer');
   // if (!trackElement) return;

    //fetch('/radio/track_title')
     //   .then(response => response.json())
      //  .then(data => {
            // Check if we actually received a valid title string
       //     if (data.is_icy && data.track_title && data.track_title.trim() !== "") {
        //        if (containerElement) containerElement.style.display = ''; // Make sure it's visible
         //       trackElement.textContent = "Now Playing: " + data.track_title;
            // } else {
                // Non-ICY or direct audio stream with no active track data
                // Option A: Show a clean, generic placeholder instead of a "waiting" message
               // trackElement.textContent = "🔊 Audio Stream Connected";

                // Option B: If you prefer to completely hide the section when there is no metadata,
                // uncomment the line below:
                // if (containerElement) containerElement.style.display = 'none';
            //}
        //})
        //.catch(err => console.error("Error fetching live track title:", err));
//}

// Poll backend every 10 seconds
//setInterval(updateRadioTrackTitle, 10000);
// Run immediately on page load
//document.addEventListener("DOMContentLoaded", updateRadioTrackTitle);

// Add or append this logic inside your static/js/radio_metadata.js file
function updateRadioTrackTitle() {
    const trackElement = document.getElementById('liveTrackTitle');
    const logoElement = document.getElementById('nowPlayingLogo');
    
    if (!trackElement) return;

    fetch('/radio/track_title')
        .then(response => response.json())
        .then(data => {
            if (data.is_icy && data.track_title && data.track_title.trim() !== "") {
                trackElement.textContent = "Now Playing: " + data.track_title;
                
                // NEW: If artwork was found, override the station logo dynamically!
                if (data.track_image && data.track_image !== "") {
                    logoElement.src = data.track_image;
                }
            //} else {
                //trackElement.textContent = "🔊 Audio Stream Connected";
            }
        })
        .catch(err => console.error("Error updating player visuals:", err));
}

// Poll backend every 10 seconds
setInterval(updateRadioTrackTitle, 10000);
// Run immediately on page load
document.addEventListener("DOMContentLoaded", updateRadioTrackTitle);


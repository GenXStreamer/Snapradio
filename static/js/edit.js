$(function() {
  $("#stationsTable").colResizable({
    liveDrag: true,
    gripInnerHtml: "<div class='grip'></div>",
    draggingClass: "dragging"
  });

$('.save-btn').click(function() {
  const rowid = $(this).data('rowid');
  const row = $('#row-' + rowid).find('td');

  let imgVal = row.eq(11).text().trim();
  if (imgVal === "Drag here") {
    imgVal = "";
  }

  const data = {
    rowid: rowid,
    Name: row.eq(1).find('input').val(),
    Code: row.eq(2).find('input').val(),
    Description: row.eq(3).find('input').val(),
    Category: row.eq(4).find('input').val(),
    Comment: row.eq(5).find('input').val(),
    Bitrate: row.eq(6).find('input').val(),
    StereoMono: row.eq(7).find('input').val(),
    StreamURL: row.eq(8).find('input').val(),
    WorkingDate: row.eq(10).find('input').val(),
    IMG: imgVal
  };

  $.ajax({
    url: '/update_station',
    type: 'POST',
    contentType: 'application/json',
    data: JSON.stringify(data),
    success: function(response) {
      // optional: console.log('Saved station ' + rowid);
    },
    error: function(err) {
      console.error('Save error: ', err.responseText);
    }
  });
});

  $('.dropzone').on('dragover', function(e) {
    e.preventDefault();
    e.stopPropagation();
    $(this).css('background-color', '#e0f7fa');
  }).on('dragleave drop', function(e) {
    e.preventDefault();
    e.stopPropagation();
    $(this).css('background-color', '');

    if (e.originalEvent.dataTransfer && e.originalEvent.dataTransfer.files.length) {
      const file = e.originalEvent.dataTransfer.files[0];
      const rowid = $(this).data('rowid');
      const dropzone = $(this);

      const formData = new FormData();
      formData.append('file', file);

      $.ajax({
        url: '/upload_logo/' + file.name,
        type: 'POST',
        data: formData,
        processData: false,
        contentType: false,
        success: function(res) {
          dropzone.text(file.name);
          // no alert
        },
        error: function(err) {
          console.error('Upload error: ', err.responseText);
        }
      });
    }
  });
});


// Search filter
$('#searchBox').on('keyup', function() {
  const value = $(this).val().toLowerCase();
  $("#stationsTable tbody tr").filter(function() {
    $(this).toggle($(this).text().toLowerCase().indexOf(value) > -1);
  });
});

// Save All button
$('#saveAllBtn').click(function() {
  $('.save-btn').each(function() {
    $(this).click();
  });
});

// Add and Cancel Buttons

$('#add-row-btn').click(function() {
  const tableBody = $('#stationsTable tbody');
  const newRowId = 'new-' + Date.now();

  const newRow = $(`
    <tr id="row-${newRowId}">
      <td>New</td>
      <td><input type="text" class="form-control form-control-sm" /></td>
      <td><input type="text" class="form-control form-control-sm" /></td>
      <td><input type="text" class="form-control form-control-sm" /></td>
      <td><input type="text" class="form-control form-control-sm" /></td>
      <td><input type="text" class="form-control form-control-sm" /></td>
      <td><input type="number" class="form-control form-control-sm" /></td>
      <td><input type="text" class="form-control form-control-sm" /></td>
      <td><input type="text" class="form-control form-control-sm" /></td>
      <td><span class="badge bg-warning text-dark">New</span></td>
      <td><input type="text" class="form-control form-control-sm" /></td>
      <td><div class="dropzone" data-rowid="${newRowId}" style="font-size: 0.8rem;">Drag here</div></td>
      <td>
        <button class="btn btn-sm btn-success save-new-btn me-1" data-rowid="${newRowId}">Add</button>
        <button class="btn btn-sm btn-secondary cancel-new-btn" data-rowid="${newRowId}">Cancel</button>
      </td>
    </tr>
  `);

  tableBody.prepend(newRow);
});


// Save button
$(document).on('click', '.save-new-btn', function() {
  const rowid = $(this).data('rowid');
  const row = $('#row-' + rowid).find('td');

  const data = {
    Name: row.eq(1).find('input').val(),
    Code: row.eq(2).find('input').val(),
    Description: row.eq(3).find('input').val(),
    Category: row.eq(4).find('input').val(),
    Comment: row.eq(5).find('input').val(),
    Bitrate: row.eq(6).find('input').val(),
    StereoMono: row.eq(7).find('input').val(),
    StreamURL: row.eq(8).find('input').val(),
    WorkingDate: row.eq(10).find('input').val(),
    IMG: row.eq(11).text().trim()
  };

  $.ajax({
    url: '/add_station',
    type: 'POST',
    contentType: 'application/json',
    data: JSON.stringify(data),
    success: function(response) {
      location.reload(); // or just update the row in-place if you prefer
    },
    error: function(err) {
      console.error('Add station error: ', err.responseText);
    }
  });
});


$(document).on('click', '.delete-btn', function() {
  const rowid = $(this).data('rowid');

  if (confirm("Are you sure you want to delete this station?")) {
    $.ajax({
      url: '/delete_station',
      type: 'POST',
      contentType: 'application/json',
      data: JSON.stringify({ rowid: rowid }),
      success: function(response) {
        $('#row-' + rowid).remove();
      },
      error: function(err) {
        console.error('Delete error: ', err.responseText);
      }
    });
  }
});

$(document).on('click', '.cancel-new-btn', function() {
  const rowid = $(this).data('rowid');
  $('#row-' + rowid).remove();
});

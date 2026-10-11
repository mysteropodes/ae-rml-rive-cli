// rml2ae: builds the layer whose gradient groups are saved as the presets of this folder. Temporary comp, safe to delete.
(function () {
  app.beginUndoGroup("rml2ae gradient template");
  var comp = app.project.items.addComp("__rml2ae_grad_template", 400, 400, 1, 2, 25);
  comp.openInViewer();
  var L = comp.layers.addShape(); L.name = "template";
  var root = L.property("ADBE Root Vectors Group");
  var g = root.addProperty("ADBE Vector Group"); g.name = "GF";
  var gc = g.property("ADBE Vectors Group");
  gc.addProperty("ADBE Vector Shape - Rect").property("ADBE Vector Rect Size").setValue([200, 120]);
  gc.addProperty("ADBE Vector Graphic - G-Fill");
  var h = root.addProperty("ADBE Vector Group"); h.name = "GS";
  var hc = h.property("ADBE Vectors Group");
  hc.addProperty("ADBE Vector Shape - Ellipse").property("ADBE Vector Ellipse Size").setValue([150, 150]);
  hc.addProperty("ADBE Vector Graphic - G-Stroke").property("ADBE Vector Stroke Width").setValue(12);
  app.endUndoGroup();
})();

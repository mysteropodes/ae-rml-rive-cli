// Builds examples/demo.aep in an EMPTY After Effects project (File > Scripts > Run Script File…).
// Synthetic content only: shape layers, text, a solid with native effects, an adjustment layer, a precomp, a track matte.
(function () {
  var here = new File($.fileName).parent;
  if (app.project && (app.project.numItems > 0 || app.project.file)) { alert("Open an empty project first (File > New > New Project)."); return; }
  if (!app.project) app.newProject();
  app.beginUndoGroup("demo");
  var W = 1280, H = 720, D = 4, FPS = 25;
  // precomp: a bouncing ball
  var ball = app.project.items.addComp("Ball", 400, 400, 1, D, FPS);
  var bl = ball.layers.addShape(); bl.name = "Ball";
  var g = bl.property("ADBE Root Vectors Group").addProperty("ADBE Vector Group");
  var el = g.property("ADBE Vectors Group").addProperty("ADBE Vector Shape - Ellipse"); el.property("ADBE Vector Ellipse Size").setValue([160, 160]);
  var fl = g.property("ADBE Vectors Group").addProperty("ADBE Vector Graphic - Fill"); fl.property("ADBE Vector Fill Color").setValue([0.95, 0.45, 0.2]);
  var bp = bl.property("ADBE Transform Group").property("ADBE Position");
  bp.setValueAtTime(0, [200, 80]); bp.setValueAtTime(1, [200, 320]); bp.setValueAtTime(2, [200, 80]); bp.setValueAtTime(3, [200, 320]); bp.setValueAtTime(4, [200, 80]);
  for (var k = 1; k <= bp.numKeys; k++) bp.setInterpolationTypeAtKey(k, KeyframeInterpolationType.BEZIER);
  // main comp
  var c = app.project.items.addComp("Demo", W, H, 1, D, FPS); c.bgColor = [0.08, 0.09, 0.12];
  var bg = c.layers.addSolid([0.15, 0.2, 0.35], "Backdrop", W, H, 1);
  var gb = bg.property("ADBE Effect Parade").addProperty("ADBE Ramp");
  var card = c.layers.addShape(); card.name = "Card";
  var cg = card.property("ADBE Root Vectors Group").addProperty("ADBE Vector Group");
  var rc = cg.property("ADBE Vectors Group").addProperty("ADBE Vector Shape - Rect"); rc.property("ADBE Vector Rect Size").setValue([520, 300]); rc.property("ADBE Vector Rect Roundness").setValue(24);
  var cf = cg.property("ADBE Vectors Group").addProperty("ADBE Vector Graphic - Fill"); cf.property("ADBE Vector Fill Color").setValue([0.96, 0.96, 0.92]);
  var cr = card.property("ADBE Transform Group").property("ADBE Rotate Z"); cr.setValueAtTime(0, -8); cr.setValueAtTime(2, 8); cr.setValueAtTime(4, -8);
  var ds = card.property("ADBE Effect Parade").addProperty("ADBE Drop Shadow");
  var txt = c.layers.addText("Rive + After Effects"); txt.name = "Title";
  var td = txt.property("ADBE Text Properties").property("ADBE Text Document"); var doc = td.value; doc.fontSize = 64; doc.fillColor = [0.1, 0.1, 0.15]; doc.justification = ParagraphJustification.CENTER_JUSTIFY; td.setValue(doc);
  txt.property("ADBE Transform Group").property("ADBE Position").setValue([W / 2, H / 2 + 20]);
  var to = txt.property("ADBE Transform Group").property("ADBE Opacity"); to.setValueAtTime(0, 0); to.setValueAtTime(0.8, 100);
  var bL = c.layers.add(ball); bL.name = "Ball precomp"; bL.property("ADBE Transform Group").property("ADBE Position").setValue([1000, 360]);
  // a wipe revealed through a track matte
  var stripe = c.layers.addSolid([0.2, 0.75, 0.6], "Stripe", W, 80, 1); stripe.property("ADBE Transform Group").property("ADBE Position").setValue([W / 2, 620]);
  var m = c.layers.addShape(); m.name = "Stripe matte";
  var mg = m.property("ADBE Root Vectors Group").addProperty("ADBE Vector Group");
  var mr = mg.property("ADBE Vectors Group").addProperty("ADBE Vector Shape - Rect"); mr.property("ADBE Vector Rect Size").setValue([W, 80]);
  mg.property("ADBE Vectors Group").addProperty("ADBE Vector Graphic - Fill");
  var ms = m.property("ADBE Transform Group").property("ADBE Scale"); ms.setValueAtTime(0, [0, 100]); ms.setValueAtTime(2, [100, 100]);
  m.property("ADBE Transform Group").property("ADBE Position").setValue([W / 2, 620]);
  m.moveBefore(stripe); stripe.setTrackMatte(m, TrackMatteType.ALPHA);
  // an adjustment layer with a native colour effect, in for the last second
  var adj = c.layers.addSolid([1, 1, 1], "Grade", W, H, 1); adj.adjustmentLayer = true; adj.name = "Grade (adjustment)";
  var tint = adj.property("ADBE Effect Parade").addProperty("ADBE Tint"); tint.property(3).setValue(60);
  adj.inPoint = 3;
  app.endUndoGroup();
  app.project.save(new File(here.fsName + "/demo.aep"));
})();

// Builds examples/fx_map.aep in an EMPTY After Effects project (File > Scripts > Run Script File…).
// Synthetic content only. Tests effects that read a SECOND layer (Displacement Map), one per kind of map layer that
// ae2rml binds: a precomp, a still image, and the layer itself.
(function () {
  var here = new File($.fileName).parent;
  if (app.project && (app.project.numItems > 0 || app.project.file)) { alert("Open an empty project first (File > New > New Project)."); return; }
  if (!app.project) app.newProject();
  app.beginUndoGroup("fx map");
  var W = 640, H = 360, D = 2, FPS = 25;

  // the map precomp: soft horizontal bands (grey levels drive the displacement)
  var wave = app.project.items.addComp("Wave map", W, H, 1, D, FPS);
  var wbg = wave.layers.addSolid([0.5, 0.5, 0.5], "Mid grey", W, H, 1);
  for (var i = 0; i < 6; i++) {
    var band = wave.layers.addSolid(i % 2 ? [1, 1, 1] : [0, 0, 0], "Band " + (i + 1), W, H / 6, 1);
    band.property("ADBE Transform Group").property("ADBE Position").setValue([W / 2, H / 12 + i * H / 6]);
    band.property("ADBE Effect Parade").addProperty("ADBE Gaussian Blur 2").property(1).setValue(20);
  }

  // the content: a gradient with a checkerboard, three times
  var c = app.project.items.addComp("Fx map", W * 3, H, 1, D, FPS);
  c.bgColor = [0.1, 0.1, 0.12];
  function content(name, x) {
    var L = c.layers.addSolid([0.3, 0.5, 0.8], name, W, H, 1);
    L.property("ADBE Transform Group").property("ADBE Position").setValue([x, H / 2]);
    var ramp = L.property("ADBE Effect Parade").addProperty("ADBE Ramp");
    ramp.property(1).setValue([0, 0]); ramp.property(3).setValue([W, H]);
    var ck = L.property("ADBE Effect Parade").addProperty("ADBE Checkerboard");
    ck.property(4).setValue(40);
    return L;
  }

  // map layers (hidden, as is usual for a displacement map)
  var waveL = c.layers.add(wave); waveL.name = "Wave map layer"; waveL.enabled = false;
  var img = app.project.importFile(new ImportOptions(new File(here.fsName + "/../rml2ae/ae2rml/fxref/src/map.png")));
  var imgL = c.layers.add(img); imgL.name = "Image map layer"; imgL.enabled = false;

  function displace(L, mapLayer, h, v) {
    var fx = L.property("ADBE Effect Parade").addProperty("ADBE Displacement Map");
    fx.property(1).setValue(mapLayer.index);     // Displacement Map Layer
    fx.property(3).setValue(h);                  // Max Horizontal Displacement
    fx.property(5).setValue(v);                  // Max Vertical Displacement
    return fx;
  }
  var a = content("Displaced by precomp", W / 2);
  var b = content("Displaced by image", W * 1.5);
  var s = content("Displaced by itself", W * 2.5);
  // every layer exists before the layer parameters are set (indices no longer move)
  displace(a, waveL, 0, 30);
  displace(b, imgL, 20, 20);
  displace(s, s, 15, 15);

  app.endUndoGroup();
  var out = new File(here.fsName + "/fx_map.aep");
  app.project.save(out);
  alert("Saved " + out.fsName + "\nCommit it, then: python -m rml2ae.ae2rml examples/fx_map.aep out/fx_map --verify");
})();

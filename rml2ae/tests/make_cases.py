"""Synthetic Rive CLI project exercising the converter's feature set (one artboard, 25 fps, 100 frames + a second state).

  python3 rml2ae/tests/make_cases.py            # -> rml2ae/tests/cases/features/  (then: rive rml2ae/tests/cases/features --verify)
"""
import os
import shutil

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
OUT = os.path.join(HERE, "cases", "features")
os.makedirs(OUT, exist_ok=True)
# the assets already in the case folder are reused (font: figma2rml/fonts, images: any two PNGs named as below)
for f in ("Montserrat-Bold.ttf", "sample_a.png", "sample_b.png"):
    src = os.path.join(OUT, f)
    if not os.path.exists(src) and f.endswith(".ttf"):
        shutil.copy(os.path.join(ROOT, "figma2rml", "fonts", f), OUT)
open(os.path.join(OUT, "rive.yaml"), "w", encoding="utf-8").write("name: features\nlogs:\n  file: build/rive.log\n  problems: build/problems.log\n")

n = [100]


def nid():
    n[0] += 1
    return f"0:{n[0]}"


def kd(frame, value, interp="linear", ease=None):
    if interp == "cubic":
        x1, y1, x2, y2 = ease or (0.5, 0, 0.05, 1)
        return f'<KeyFrameDouble value="{value}" frame="{frame}" interpolationType="cubic"><CubicEaseInterpolator x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}"/></KeyFrameDouble>'
    return f'<KeyFrameDouble value="{value}" frame="{frame}" interpolationType="{interp}"/>'


IDS = {k: nid() for k in ("ab", "anim1", "anim2", "sm", "layer", "st1", "st2", "font", "img1", "img2",
                          "gradRect", "grad", "stop0", "stop1", "gradEll", "grp", "grpA", "grpB", "txt", "run", "style",
                          "img", "solo", "s1", "s2", "s3", "root", "b1", "b2", "sq1", "sq2", "skinShape", "skinPath",
                          "v0", "v1", "v2", "v3", "ikRoot", "ikB1", "ikB2", "ikTarget", "tanShape", "tanPath", "tv0", "tv1",
                          "dashShape", "dash", "hidden", "layout", "lstyle", "abstyle", "grp2")}
I = IDS

anim1 = []      # KeyedObject xml
anim2 = []


def keyed(anim, oid, pk, frames):
    anim.append(f'<KeyedObject objectId="{oid}"><KeyedProperty propertyKey="{pk}">' + "".join(frames) + "</KeyedProperty></KeyedObject>")


body = []
# 1. gradients: linear 2 stops keyed colours (rect), radial 4 stops (ellipse)
body.append(f'''<Shape x="200" y="150" name="Grad rect" id="{I["gradRect"]}"><Rectangle width="300" height="200" name="R"/>
<Fill name="Fill"><LinearGradient startX="-150" startY="0" endX="150" endY="0" name="G" id="{I["grad"]}">
<GradientStop colorValue="FFFF0000" position="0" id="{I["stop0"]}"/><GradientStop colorValue="FF0000FF" position="1" id="{I["stop1"]}"/></LinearGradient></Fill></Shape>''')
keyed(anim1, I["stop0"], 38, ['<KeyFrameColor value="FFFF0000" frame="0" interpolationType="linear"/>', '<KeyFrameColor value="FF00FF00" frame="60" interpolationType="linear"/>'])
body.append(f'''<Shape x="600" y="150" name="Grad ellipse" id="{I["gradEll"]}"><Ellipse width="260" height="200" name="E"/>
<Fill name="Fill"><RadialGradient startX="0" startY="0" endX="130" endY="0" name="G">
<GradientStop colorValue="FFFFFFFF" position="0"/><GradientStop colorValue="FFFFD700" position="0.4"/><GradientStop colorValue="FFFF4500" position="0.8"/><GradientStop colorValue="FF400000" position="1"/></RadialGradient></Fill></Shape>''')
# 2. group opacity keyed, two overlapping shapes
body.append(f'''<Node x="950" y="150" opacity="0.5" name="Group" id="{I["grp"]}">
<Shape x="0" y="0" name="A" id="{I["grpA"]}"><Rectangle width="200" height="200" name="R"/><Fill name="F"><SolidColor colorValue="FF2266FF" name="C"/></Fill></Shape>
<Shape x="80" y="60" name="B" id="{I["grpB"]}"><Ellipse width="200" height="200" name="E"/><Fill name="F"><SolidColor colorValue="FFFF2266" name="C"/></Fill></Shape></Node>''')
keyed(anim1, I["grp"], 18, [kd(0, 0.5, "hold"), kd(40, 0.5, "cubic"), kd(80, 1.0, "hold")])
# 3. text changing over time
body.append(f'''<Text x="1300" y="120" sizingValue="autoHeight" width="500" alignValue="center" name="Counter" id="{I["txt"]}">
<TextStylePaint fontSize="72" fontAssetId="{I["font"]}" name="Style" id="{I["style"]}"><Fill name="Fill"><SolidColor colorValue="FF182241" name="C"/></Fill></TextStylePaint>
<TextValueRun styleId="{I["style"]}" text="ONE" name="Run" id="{I["run"]}"/></Text>''')
keyed(anim1, I["run"], 268, ['<KeyFrameString value="ONE" frame="0" interpolationType="hold"/>', '<KeyFrameString value="TWO" frame="30" interpolationType="hold"/>', '<KeyFrameString value="THREE" frame="60" interpolationType="hold"/>'])
# 4. image swap
body.append(f'<Image x="300" y="520" scaleX="0.3" scaleY="0.3" assetId="{I["img1"]}" name="Swap" id="{I["img"]}"/>')
keyed(anim1, I["img"], 206, [f'<KeyFrameId value="{I["img1"]}" frame="0" interpolationType="hold"/>', f'<KeyFrameId value="{I["img2"]}" frame="50" interpolationType="hold"/>'])
# 5. Solo keyed
body.append(f'''<Solo x="700" y="520" activeComponentId="{I["s1"]}" name="Solo" id="{I["solo"]}">
<Shape name="S1" id="{I["s1"]}"><Rectangle width="120" height="120" name="R"/><Fill name="F"><SolidColor colorValue="FFFF0000" name="C"/></Fill></Shape>
<Shape name="S2" id="{I["s2"]}"><Ellipse width="120" height="120" name="E"/><Fill name="F"><SolidColor colorValue="FF00AA00" name="C"/></Fill></Shape>
<Shape name="S3" id="{I["s3"]}"><Star width="120" height="120" points="5" innerRadius="0.5" name="St"/><Fill name="F"><SolidColor colorValue="FF0000FF" name="C"/></Fill></Shape></Solo>''')
keyed(anim1, I["solo"], 296, [f'<KeyFrameId value="{I["s1"]}" frame="0" interpolationType="hold"/>', f'<KeyFrameId value="{I["s2"]}" frame="33" interpolationType="hold"/>', f'<KeyFrameId value="{I["s3"]}" frame="66" interpolationType="hold"/>'])
# 6. rigid bones with parented squares
body.append(f'''<RootBone x="1000" y="620" length="120" rotation="0" name="Root" id="{I["root"]}">
<Shape x="60" y="0" name="Seg1" id="{I["sq1"]}"><Rectangle width="110" height="24" name="R"/><Fill name="F"><SolidColor colorValue="FF444444" name="C"/></Fill></Shape>
<Bone length="100" rotation="0" name="B1" id="{I["b1"]}">
<Shape x="50" y="0" name="Seg2" id="{I["sq2"]}"><Rectangle width="90" height="18" name="R"/><Fill name="F"><SolidColor colorValue="FF888888" name="C"/></Fill></Shape>
<Bone length="60" rotation="0" name="B2" id="{I["b2"]}"/></Bone></RootBone>''')
keyed(anim1, I["root"], 15, [kd(0, 0.0, "cubic"), kd(50, -0.8, "cubic"), kd(100, 0.0, "hold")])
keyed(anim1, I["b1"], 15, [kd(0, 0.0, "cubic"), kd(50, 1.2, "cubic"), kd(100, 0.0, "hold")])
# 7. skinned path on two bones (doc example, bound identity)
body.append(f'''<Shape name="Skinned" id="{I["skinShape"]}"><PointsPath isClosed="true" name="Path" id="{I["skinPath"]}">
<StraightVertex x="1000" y="600" id="{I["v0"]}"><Weight values="255" indices="1"/></StraightVertex>
<StraightVertex x="1120" y="600" id="{I["v1"]}"><Weight values="32896" indices="513"/></StraightVertex>
<StraightVertex x="1220" y="640" id="{I["v2"]}"><Weight values="255" indices="2"/></StraightVertex>
<StraightVertex x="1000" y="640" id="{I["v3"]}"><Weight values="255" indices="1"/></StraightVertex>
<Skin tx="0" ty="0" name="Skin"><Tendon boneId="{I["root"]}" tx="1000" ty="620" name="T0"/><Tendon boneId="{I["b1"]}" tx="1120" ty="620" name="T1"/></Skin></PointsPath>
<Fill name="F"><SolidColor colorValue="8000AA88" name="C"/></Fill></Shape>''')
# 8. IK chain aiming at a moving target
body.append(f'''<Node x="1500" y="700" name="IK target" id="{I["ikTarget"]}"/>
<RootBone x="1400" y="900" length="120" rotation="-1.2" name="IK root" id="{I["ikRoot"]}"><Shape x="60" y="0" name="IK seg1"><Rectangle width="110" height="20" name="R"/><Fill name="F"><SolidColor colorValue="FFAA5500" name="C"/></Fill></Shape>
<Bone length="120" rotation="0.6" name="IK b1" id="{I["ikB1"]}"><Shape x="60" y="0" name="IK seg2"><Rectangle width="110" height="16" name="R"/><Fill name="F"><SolidColor colorValue="FFFF8800" name="C"/></Fill></Shape>
<IKConstraint targetId="{I["ikTarget"]}" parentBoneCount="1" strength="1" name="IK"/></Bone></RootBone>''')
keyed(anim1, I["ikTarget"], 13, [kd(0, 1500, "cubic"), kd(50, 1700, "cubic"), kd(100, 1500, "hold")])
keyed(anim1, I["ikTarget"], 14, [kd(0, 700, "cubic"), kd(50, 820, "cubic"), kd(100, 700, "hold")])
# 9. tangent keys
body.append(f'''<Shape x="200" y="850" name="Tangents" id="{I["tanShape"]}"><PointsPath isClosed="false" name="P" id="{I["tanPath"]}">
<CubicDetachedVertex x="0" y="0" inRotation="3.14" inDistance="60" outRotation="0" outDistance="60" id="{I["tv0"]}"/>
<CubicDetachedVertex x="300" y="0" inRotation="3.14" inDistance="60" outRotation="0" outDistance="60" id="{I["tv1"]}"/></PointsPath>
<Stroke thickness="8" cap="round" name="S"><SolidColor colorValue="FF111111" name="C"/></Stroke></Shape>''')
keyed(anim1, I["tv0"], 86, [kd(0, 0.0, "cubic"), kd(50, 1.4, "cubic"), kd(100, 0.0, "hold")])
keyed(anim1, I["tv1"], 84, [kd(0, 3.14, "cubic"), kd(50, 4.5, "cubic"), kd(100, 3.14, "hold")])
# 10. dash offset keyed + hidden drawable
body.append(f'''<Shape x="600" y="850" name="Dashes" id="{I["dashShape"]}"><PointsPath isClosed="false" name="P"><StraightVertex x="0" y="0"/><StraightVertex x="300" y="0"/></PointsPath>
<Stroke thickness="6" name="S"><SolidColor colorValue="FF0055AA" name="C"/><DashPath name="D" id="{I["dash"]}"><Dash length="20" name="On"/><Dash length="14" name="Off"/></DashPath></Stroke></Shape>''')
keyed(anim1, I["dash"], 690, [kd(0, 0.0, "linear"), kd(100, 68.0, "hold")])
body.append(f'<Shape x="600" y="950" drawableFlags="1" name="Hidden" id="{I["hidden"]}"><Rectangle width="200" height="40" name="R"/><Fill name="F"><SolidColor colorValue="FFFF0000" name="C"/></Fill></Shape>')
# 11. flex layout (replayed)
body.append(f'''<LayoutComponent x="1000" y="880" width="500" height="120" styleId="{I["lstyle"]}" name="Flex" id="{I["layout"]}">
<LayoutComponentStyle flexDirectionValue="row" gapHorizontal="10" name="Row" id="{I["lstyle"]}"/>
<LayoutComponent width="100" height="100" name="Cell1"><Shape name="C1"><Rectangle originX="0" originY="0" width="100" height="100" name="R"/><Fill name="F"><SolidColor colorValue="FF33AA33" name="C"/></Fill></Shape></LayoutComponent>
<LayoutComponent width="100" height="100" name="Cell2"><Shape name="C2"><Rectangle originX="0" originY="0" width="100" height="100" name="R"/><Fill name="F"><SolidColor colorValue="FFAA3333" name="C"/></Fill></Shape></LayoutComponent>
<LayoutComponent width="100" height="100" name="Cell3"><Shape name="C3"><Rectangle originX="0" originY="0" width="100" height="100" name="R"/><Fill name="F"><SolidColor colorValue="FF3333AA" name="C"/></Fill></Shape></LayoutComponent></LayoutComponent>''')
# second animation (state 2): everything shifts, the group fades
keyed(anim2, I["grp"], 13, [kd(0, 950, "cubic"), kd(50, 1100, "hold")])
keyed(anim2, I["gradRect"], 15, [kd(0, 0.0, "linear"), kd(50, 0.5, "hold")])

rml = f'''<Rive version="1" kind="fragment">
<Artboard styleId="{I["abstyle"]}" defaultStateMachineId="{I["sm"]}" width="1920" height="1080" name="Features" id="{I["ab"]}">
<LayoutComponentStyle name="Artboard Style" id="{I["abstyle"]}"/>
<Fill name="Background"><SolidColor colorValue="FFF3F0E8" name="C"/></Fill>
{chr(10).join(body)}
<LinearAnimation fps="25" duration="100" loopValue="oneShot" quantize="true" name="Anim A" id="{I["anim1"]}">{"".join(anim1)}</LinearAnimation>
<LinearAnimation fps="25" duration="50" loopValue="oneShot" name="Anim B" id="{I["anim2"]}">{"".join(anim2)}</LinearAnimation>
<StateMachine name="SM" id="{I["sm"]}"><StateMachineLayer name="L" id="{I["layer"]}">
<AnyState x="200" y="-120"/><ExitState x="600" y="-120"/><EntryState x="0" y="0"><StateTransition stateToId="{I["st1"]}"/></EntryState>
<AnimationState x="200" y="0" animationId="{I["anim1"]}" id="{I["st1"]}"><StateTransition stateToId="{I["st2"]}" enableExitTime="true" exitTimeIsPercetange="true" exitTime="80" duration="600"/></AnimationState>
<AnimationState x="400" y="0" animationId="{I["anim2"]}" id="{I["st2"]}"/>
</StateMachineLayer></StateMachine>
</Artboard>
<FontAsset file="Montserrat-Bold.ttf" name="Montserrat-Bold" id="{I["font"]}"/>
<ImageAsset file="sample_a.png" name="sample_a" id="{I["img1"]}"/>
<ImageAsset file="sample_b.png" name="sample_b" id="{I["img2"]}"/>
</Rive>'''
open(os.path.join(OUT, "scene.rml"), "w", encoding="utf-8").write(rml)
print("wrote", os.path.join(OUT, "scene.rml"))

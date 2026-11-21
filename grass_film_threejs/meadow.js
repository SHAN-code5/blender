import * as THREE from 'three';

export function seeded(seed) {
  return () => { seed = (Math.imul(seed, 1664525) + 1013904223) >>> 0; return seed / 4294967296; };
}

export function createMeadow() {
  const rng = seeded(730);
  const scene = new THREE.Scene();
  scene.fog = new THREE.FogExp2(0x9ca99b, .045);
  const uniforms = { time: { value: 0 }, wind: { value: 1 }, golden: { value: 0 }, macro: { value: 0 } };
  const windVertex=`
    vec3 origin = instanceMatrix[3].xyz;
    float wave=sin(origin.x*.42+origin.z*.35-uTime*.95);
    float ripple=sin(origin.x*1.4-origin.z*.6+uTime*1.8)*.22;
    transformed.x += position.y*position.y*(wave+ripple)*.17*uWind;
    transformed.z += position.y*position.y*sin(origin.x*.31+origin.z*.22-uTime*.74)*.08*uWind;
    if(uMacro>.5 && abs(origin.x)<1.8 && origin.z<1.7) transformed.y-=100.;
  `;
  const root = new THREE.Group();
  scene.add(root);

  const skyMat = new THREE.ShaderMaterial({
    side: THREE.BackSide, depthWrite: false,
    uniforms: { top: { value: new THREE.Color('#91afbb') }, bottom: { value: new THREE.Color('#e3d8b4') }, sunColor: { value: new THREE.Color('#ffe7ad') }, sunPower: { value: .5 } },
    vertexShader: 'varying vec3 vDir; void main(){vDir=position; gl_Position=projectionMatrix*modelViewMatrix*vec4(position,1.);}',
    fragmentShader: `varying vec3 vDir; uniform vec3 top,bottom,sunColor; uniform float sunPower;
      void main(){vec3 d=normalize(vDir); float h=smoothstep(-.04,.6,d.y); vec3 c=mix(bottom,top,h);
      float sun=pow(max(dot(d,normalize(vec3(-.45,.13,.88))),0.),150.);
      c+=sunColor*sun*sunPower; gl_FragColor=vec4(c,1.);}`,
  });
  scene.add(new THREE.Mesh(new THREE.SphereGeometry(100, 24, 12), skyMat));
  const hemi = new THREE.HemisphereLight(0xcbd9d4, 0x253009, 1.4);
  scene.add(hemi);
  const sun = new THREE.DirectionalLight(0xffdb9c, 3.3);
  sun.position.set(-6, 6, 10); scene.add(sun);
  const fill = new THREE.DirectionalLight(0x92afbc, .35);
  fill.position.set(3, 4, -5); scene.add(fill);

  const ground = new THREE.Mesh(new THREE.PlaneGeometry(90, 90), new THREE.MeshStandardMaterial({ color: 0x19250b, roughness: 1 }));
  ground.rotation.x = -Math.PI / 2; ground.position.y = -.025; root.add(ground);

  const positions = [], indices = [];
  for (let j = 0; j <= 7; j++) {
    const h = j / 7, width = .038 * (1 - h) * (.7 + .3 * Math.sin(h * Math.PI));
    const bend = h * h * .30;
    positions.push(-width, h, bend, 0, h + .001, bend - .008 * Math.sin(h * Math.PI), width, h, bend);
    if (j) { const a = (j - 1) * 3; indices.push(a,a+3,a+4,a,a+4,a+1,a+1,a+4,a+5,a+1,a+5,a+2); }
  }
  const blade = new THREE.BufferGeometry();
  blade.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3)); blade.setIndex(indices); blade.computeVertexNormals();
  blade.setAttribute('grassBlade',new THREE.Float32BufferAttribute(new Float32Array(positions.length/3).fill(1),1));
  const grassMat = new THREE.MeshStandardMaterial({ color: 0xffffff, roughness: .68, metalness: 0, side: THREE.DoubleSide });
  grassMat.onBeforeCompile = shader => {
    shader.uniforms.uTime = uniforms.time; shader.uniforms.uWind = uniforms.wind; shader.uniforms.uGolden = uniforms.golden;shader.uniforms.uMacro=uniforms.macro;
    shader.vertexShader = `uniform float uTime,uWind,uMacro; varying float vHeight;\n` + shader.vertexShader;
    shader.vertexShader = shader.vertexShader.replace('#include <begin_vertex>', `
      #include <begin_vertex>
      ${windVertex}
      vHeight=position.y;
    `);
    shader.fragmentShader = `uniform float uGolden; varying float vHeight;\n` + shader.fragmentShader;
    shader.fragmentShader = shader.fragmentShader.replace('#include <color_fragment>', `
      #include <color_fragment>
      diffuseColor.rgb = mix(diffuseColor.rgb, diffuseColor.rgb*vec3(2.05,1.05,.38),uGolden);
      diffuseColor.rgb *= mix(.25,1.,smoothstep(0.,.7,vHeight));
      diffuseColor.rgb += vec3(.015,.023,.002)*pow(vHeight,2.);
      totalEmissiveRadiance+=diffuseColor.rgb*.16;
    `);
  };
  const grass = new THREE.InstancedMesh(blade, grassMat, 65000);
  grass.frustumCulled = false;
  const dummy = new THREE.Object3D(), color = new THREE.Color();
  for (let i = 0; i < grass.count; i++) {
    const x = (rng() - .5) * 44, z = rng() * 49 - 14;
    dummy.position.set(x, .035 * Math.sin(x*.4) * Math.sin(z*.3), z);
    dummy.rotation.set((rng()-.5)*.15, rng()*Math.PI*2, (rng()-.5)*.2);
    const h = .45 + rng() * 1.1;
    dummy.scale.setScalar(h); dummy.scale.x *= .6+rng()*.6; dummy.updateMatrix(); grass.setMatrixAt(i, dummy.matrix);
    const c = rng(); color.setRGB(.065+.15*c, .14+.23*c, .012+.042*c); grass.setColorAt(i, color);
  }
  root.add(grass);

  const seeds = new THREE.Group(); root.add(seeds);
  const stemMat = new THREE.MeshStandardMaterial({ color: 0x778637, roughness: .7 });
  const seedMat = new THREE.MeshStandardMaterial({ color: 0xb3a166, roughness: .75, emissive:0x61522d,emissiveIntensity:.4 });
  const count = 110, heads = [], seedCount = count * 14;
  const stems = new THREE.InstancedMesh(new THREE.CylinderGeometry(.004,.007,1,5), stemMat, count);
  const grains = new THREE.InstancedMesh(new THREE.SphereGeometry(1,8,6), seedMat, seedCount);
  const awnPositions = new Float32Array(seedCount * 6);
  for (let i=0;i<count;i++) {
    const x = i<3 ? [-.45,.2,.68][i] : (rng()-.5)*27;
    const z = i<3 ? [0,.12,.6][i] : rng()*24-2;
    const h = i<3 ? [1.72,1.94,1.63][i] : 1.3+rng()*.85;
    heads.push({x,z,h,phase:rng()*6.28});
  }
  const awns = new THREE.LineSegments(new THREE.BufferGeometry(), new THREE.LineBasicMaterial({ color: 0xc1b184, transparent:true, opacity:.5 }));
  awns.geometry.setAttribute('position',new THREE.BufferAttribute(awnPositions,3));
  seeds.add(stems,grains,awns);
  function updateSeeds(t) {
    for(let i=0;i<count;i++) {
      const a=heads[i], bend=Math.sin(t*.9+a.x*.4+a.z*.3)*.045*uniforms.wind.value;
      dummy.position.set(a.x+bend*.4,a.h/2,a.z);dummy.rotation.set(0,0,-bend);dummy.scale.set(1,a.h,1);dummy.updateMatrix();stems.setMatrixAt(i,dummy.matrix);
      for(let j=0;j<14;j++) {
        const side=j%2?1:-1, y=a.h-.33+j*.025;
        const x=a.x+side*(.028+.019*Math.sin(j*.25))+bend*y;
        dummy.position.set(x,y,a.z);dummy.rotation.set(0,0,-side*.42-bend);dummy.scale.set(.016,.039,.013);dummy.updateMatrix();
        const k=i*14+j;grains.setMatrixAt(k,dummy.matrix);
        awnPositions.set([x,y+.02,a.z,x+side*.047+bend*.1,y+.13,a.z+.008],k*6);
      }
    }
    stems.instanceMatrix.needsUpdate=true;grains.instanceMatrix.needsUpdate=true;awns.geometry.attributes.position.needsUpdate=true;
  }

  const dew = new THREE.Group(); scene.add(dew);
  const heroMat = new THREE.MeshStandardMaterial({ color:0x4c7319, roughness:.35, side:THREE.DoubleSide });
  const heroGeo=blade.clone();heroGeo.deleteAttribute('grassBlade');
  const heroPos=heroGeo.attributes.position;
  for(let i=0;i<heroPos.count;i++){const y=heroPos.getY(i);heroPos.setXYZ(i,heroPos.getX(i)*1.6+.4*y*y,y,heroPos.getZ(i)*.02);}
  heroGeo.computeVertexNormals();
  const hero = new THREE.Mesh(heroGeo,heroMat);hero.scale.y=1.45;dew.add(hero);
  // A refractive environment is procedural too: sky above a blurred green horizon.
  const envScene=new THREE.Scene();
  const envMat=new THREE.ShaderMaterial({side:THREE.BackSide,vertexShader:'varying vec3 d;void main(){d=position;gl_Position=projectionMatrix*modelViewMatrix*vec4(position,1.);}',fragmentShader:`varying vec3 d;void main(){vec3 v=normalize(d);vec3 c=mix(vec3(.13,.24,.025),vec3(.72,.83,.9),smoothstep(-.2,.2,v.y));c+=vec3(.6,.5,.3)*pow(max(dot(v,normalize(vec3(-.4,.7,-.3))),0.),70.);gl_FragColor=vec4(c,1.);}`});
  envScene.add(new THREE.Mesh(new THREE.SphereGeometry(8,24,12),envMat));
  const waterMat = new THREE.MeshPhysicalMaterial({ color:0xe6f2ef, roughness:.025, metalness:0, transmission:.93, thickness:.14, ior:1.333, envMapIntensity:1.4, clearcoat:1 });
  for(const [x,y,z,r] of [[.22,1.04,-.025,.069],[.29,1.18,-.02,.031],[.16,.89,-.025,.023]]) {
    const drop = new THREE.Mesh(new THREE.SphereGeometry(r,32,24),waterMat);drop.position.set(x,y,z);dew.add(drop);
  }
  const glintPositions=[];
  for(let i=0;i<65;i++)glintPositions.push(rng()*5-2.5,.3+rng()*2.2,2+rng()*8);
  const glintGeo=new THREE.BufferGeometry();glintGeo.setAttribute('position',new THREE.Float32BufferAttribute(glintPositions,3));
  const glints=new THREE.Points(glintGeo,new THREE.PointsMaterial({color:0xe4eab5,size:.035,transparent:true,opacity:.8,depthWrite:false}));dew.add(glints);

  const rainCount=450, rainData=new Float32Array(rainCount*6), rainSeeds=[];
  for(let i=0;i<rainCount;i++)rainSeeds.push([rng()*9-4.5,rng()*5,rng()*12-4]);
  const rainGeo=new THREE.BufferGeometry();rainGeo.setAttribute('position',new THREE.BufferAttribute(rainData,3));
  const rain = new THREE.LineSegments(rainGeo,new THREE.LineBasicMaterial({color:0xbacbd0,transparent:true,opacity:.42,depthWrite:false}));scene.add(rain);rain.frustumCulled=false;
  function updateRain(t){
    for(let i=0;i<rainCount;i++) {
      const [x,y,z]=rainSeeds[i], yy=((y-t*3.8)%5+5)%5;
      rainData.set([x+Math.sin(t*.3)*.2,yy,z,x+.025+Math.sin(t*.3)*.2,yy+.18,z],i*6);
    }
    rainGeo.attributes.position.needsUpdate=true;
  }

  const fireflyCount=75, fireflyData=new Float32Array(fireflyCount*3), fireflySeeds=[];
  for(let i=0;i<fireflyCount;i++)fireflySeeds.push([rng()*14-7,.7+rng()*2,rng()*18-2,rng()*6.28]);
  const flyGeo=new THREE.BufferGeometry();flyGeo.setAttribute('position',new THREE.BufferAttribute(fireflyData,3));
  const fireflies=new THREE.Points(flyGeo,new THREE.ShaderMaterial({transparent:true,depthWrite:false,blending:THREE.AdditiveBlending,uniforms:{time:uniforms.time},vertexShader:`uniform float time;varying float pulse;void main(){vec4 p=modelViewMatrix*vec4(position,1.);pulse=.5+.5*sin(time*1.8+position.x*2.);gl_PointSize=clamp(75./(-p.z),3.,22.)*(.7+.3*pulse);gl_Position=projectionMatrix*p;}`,fragmentShader:`varying float pulse;void main(){float r=length(gl_PointCoord-.5)*2.;if(r>1.)discard;float a=exp(-r*r*5.)*(.3+.7*pulse);gl_FragColor=vec4(vec3(1.,1.,.25)*1.8,a);}`}));
  fireflies.frustumCulled=false;scene.add(fireflies);
  function updateFireflies(t){for(let i=0;i<fireflyCount;i++){const [x,y,z,p]=fireflySeeds[i];fireflyData.set([x+Math.sin(t*.4+p)*.3,y+Math.sin(t*.75+p)*.18,z+Math.cos(t*.3+p)*.3],i*3);}flyGeo.attributes.position.needsUpdate=true;}
  return { scene, uniforms, skyMat, sun, hemi, fill, grassMat, seedMat, stemMat, dew, rain, fireflies, waterMat, envScene,
    configureDepth(material){
      material.side=THREE.DoubleSide;material.defaultAttributeValues={grassBlade:[0]};
      material.onBeforeCompile=shader=>{
        shader.uniforms.uTime=uniforms.time;shader.uniforms.uWind=uniforms.wind;shader.uniforms.uMacro=uniforms.macro;
        shader.vertexShader='uniform float uTime,uWind,uMacro;attribute float grassBlade;\n'+shader.vertexShader;
        shader.vertexShader=shader.vertexShader.replace('#include <begin_vertex>',`#include <begin_vertex>\n#ifdef USE_INSTANCING\nif(grassBlade>.5){${windVertex}}\n#endif`);
      };
    },
    update(t,shot){uniforms.time.value=t;uniforms.macro.value=shot===2?1:0;dew.visible=shot===2;rain.visible=shot===5;fireflies.visible=shot===8;updateSeeds(t);if(rain.visible)updateRain(t);if(fireflies.visible)updateFireflies(t);},
  };
}

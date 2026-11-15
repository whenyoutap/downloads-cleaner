three.js r160 (npm three@0.160.0, MIT, see LICENSE), checked against the npm registry's sha512 integrity on 15 Nov 2026.
Copied: build/three.module.min.js and the post-processing files the bloom effect needs (examples/jsm/postprocessing, examples/jsm/shaders).
One change: the add-ons' `from 'three'` imports point at ../../build/three.module.min.js, so the page needs no inline import map
(the dashboard's Content-Security-Policy allows no inline scripts).

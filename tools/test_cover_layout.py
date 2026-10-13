"""No padding or cropping may be introduced into full-tile cover assets."""
import io
from PIL import Image
from enrich_database import normalize_cover

for size in [(100,240),(240,100),(256,256)]:
    im=Image.new('RGB',size,(230,60,25))
    # Distinct corners show that all of the original artwork survives stretching.
    for box,color in [((0,0,20,20),(255,255,255)),((size[0]-20,size[1]-20,*size),(20,200,60))]:
        im.paste(color,box)
    out=io.BytesIO(); im.save(out,format='PNG')
    decoded=Image.open(io.BytesIO(normalize_cover(out.getvalue())))
    assert decoded.size==(256,256)
    assert all(v>230 for v in decoded.getpixel((2,2)))
    assert decoded.getpixel((253,253))[1]>170
    assert decoded.getpixel((0,128))[0]>200
    assert decoded.getpixel((128,0))[0]>200
print('PASS portrait, landscape and square artwork fills every tile edge; no bars or cropped corners')

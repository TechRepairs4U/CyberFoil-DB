"""Developer-only contact sheet of real output tiles, not generated artwork."""
import io
from PIL import Image, ImageDraw, ImageFont
import enrich_database as db

records=db.unpack_icons(db.ROOT/'icons.pack')
rows=db.read_json(db.ROOT/'catalogue-artwork.json')['entries']
wanted=[('ports','Grand Theft Auto V'),('ports','Bully - Anniversary Edition (Port)'),
 ('ports','Simpsons Hit Run Port'),('ports','Half-Life 2'),('ports','Fallout 2 CE'),
 ('ports','The Legend of Zelda Twilight Princess'),('ports','Castlevania Symphony of the Night (Android Port)'),
 ('ports','Sonic Unleashed'),('32x','Blackthorne USA'),('sg','Golden Axe World')]
chosen=[next(r for r in rows if r[0]==p and r[2]==n) for p,n in wanted]
for system in ('3ds','nds','nes','psp','sms','snes'): chosen.append(next(r for r in rows if r[0]==system))
sheet=Image.new('RGB',(4*286,4*320),(25,25,25)); draw=ImageDraw.Draw(sheet)
font=ImageFont.truetype('C:/Windows/Fonts/arial.ttf',15)
for i,(system,ident,name,tid) in enumerate(chosen):
    with Image.open(io.BytesIO(records[tid][1])) as im: face=im.convert('RGB').resize((256,256))
    x=(i%4)*286+15; y=(i//4)*320+10
    sheet.paste(face,(x,y))
    draw.text((x,y+261),system.upper()+': '+name[:30],font=font,fill='white')
    if len(name)>30: draw.text((x,y+281),name[30:63],font=font,fill='white')
path=db.CACHE/'catalogue-repair/verified-artwork-preview.jpg'; sheet.save(path,quality=92)
print(path)

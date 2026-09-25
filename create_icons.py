from PIL import Image, ImageDraw
import os

def create_app_icon(output_path="app_icon.ico"):
    # Create 256x256 icon
    size = (256, 256)
    img = Image.new('RGBA', size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    
    # Outer dark circle
    draw.ellipse((8, 8, 248, 248), fill=(24, 28, 36, 255), outline=(59, 130, 246, 255), width=8)
    
    # Camera body in center
    # Top lens bump
    draw.rounded_rectangle((90, 60, 166, 90), radius=10, fill=(75, 85, 99, 255))
    # Body
    draw.rounded_rectangle((50, 85, 206, 195), radius=20, fill=(37, 99, 235, 255))
    # Lens outer
    draw.ellipse((95, 105, 161, 171), fill=(17, 24, 39, 255), outline=(96, 165, 250, 255), width=6)
    # Lens center reflection
    draw.ellipse((112, 122, 144, 154), fill=(59, 130, 246, 255))
    draw.ellipse((117, 127, 127, 137), fill=(255, 255, 255, 200))
    
    # Sparkle / AI star in top right
    draw.ellipse((180, 50, 210, 80), fill=(245, 158, 11, 255))
    
    img.save(output_path, format='ICO', sizes=[(16,16), (32,32), (48,48), (64,64), (128,128), (256,256)])
    print(f"Saved {output_path}")

def get_tray_icon(status="active"):
    # 64x64 tray icon
    img = Image.new('RGBA', (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    
    # Circle background
    draw.ellipse((4, 4, 60, 60), fill=(20, 24, 33, 255), outline=(40, 48, 64, 255), width=2)
    
    if status == "active":
        color = (34, 197, 94, 255) # Green
    elif status == "paused":
        color = (234, 179, 8, 255) # Yellow/Orange
    else:
        color = (156, 163, 175, 255) # Gray
        
    # Inner glowing indicator
    draw.ellipse((18, 18, 46, 46), fill=color)
    draw.ellipse((22, 22, 30, 30), fill=(255, 255, 255, 180))
    return img

if __name__ == '__main__':
    create_app_icon("c:/_CODE/Utilites/ImmichCaptioner/app_icon.ico")

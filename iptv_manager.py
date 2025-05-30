#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import json
import subprocess
import threading
import socket
import glob
import http.server
import socketserver
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext
import logging
import traceback
import datetime
import requests
import xml.etree.ElementTree as ET
import re
from urllib.parse import quote
import time
import unicodedata
from datetime import datetime, timedelta

# Hata günlüğü ayarları
log_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
if not os.path.exists(log_dir):
    os.makedirs(log_dir)

log_file = os.path.join(log_dir, f"iptv_manager_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log")

# Kök logger'ı yapılandır
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(log_file, encoding='utf-8'),
        logging.StreamHandler()
    ]
)

logger = logging.getLogger(__name__)
logger.info("IPTV Manager başlatılıyor...")

# Beklenmeyen hataları yakala ve kaydet
def handle_exception(exc_type, exc_value, exc_traceback):
    if issubclass(exc_type, KeyboardInterrupt):
        # Ctrl+C ile çıkış durumunda normal davranış
        sys.__excepthook__(exc_type, exc_value, exc_traceback)
        return
    
    error_msg = "".join(traceback.format_exception(exc_type, exc_value, exc_traceback))
    logger.error(f"Beklenmeyen hata: {error_msg}")
    try:
        # GUI hala çalışıyorsa mesaj göster
        messagebox.showerror("Hata", 
                           f"Beklenmeyen bir hata oluştu!\n\n"
                           f"{exc_value}\n\n"
                           f"Hata detayları log dosyasına kaydedildi:\n{log_file}")
    except:
        # GUI çalışmıyorsa konsola yaz
        print(f"Beklenmeyen hata: {error_msg}")
        print(f"Detaylar log dosyasına kaydedildi: {log_file}")

# Global hata yakalayıcı
sys.excepthook = handle_exception

# Uygulamayı başlatmadan önce gerekli kontroller
def check_requirements():
    """Gerekli modüllerin yüklü olup olmadığını kontrol et"""
    try:
        # FFmpeg kontrolü
        try:
            subprocess.run(['ffmpeg', '-version'], 
                          stdout=subprocess.PIPE, 
                          stderr=subprocess.PIPE, 
                          check=True)
            logger.info("FFmpeg kontrolü başarılı")
        except (subprocess.SubprocessError, FileNotFoundError) as e:
            logger.error(f"FFmpeg bulunamadı: {str(e)}")
            messagebox.showerror(
                "FFmpeg Bulunamadı",
                "FFmpeg yüklü değil veya PATH'e eklenmemiş.\n\n"
                "Lütfen FFmpeg'i yükleyin ve PATH'e ekleyin:\n"
                "https://ffmpeg.org/download.html"
            )
            return False
        
        return True
    except Exception as e:
        logger.error(f"Gereksinim kontrolü hatası: {str(e)}")
        messagebox.showerror("Gereksinim Kontrolü Hatası", str(e))
        return False

class TMDBVideoEPGGenerator:
    def __init__(self):
        self.video_files = []
        self.channels = {}
        self.TMDB_API_KEY = "6126fc134d763a314ae9a08f5da38bde"
        self.TMDB_BASE_URL = "https://api.themoviedb.org/3"
    
    # format_size metodu ekliyorum - hata giderimi için
    def format_size(self, size_bytes):
        """Dosya boyutunu okunabilir formata çevir"""
        if size_bytes < 1024:
            return f"{size_bytes} B"
        elif size_bytes < 1024 * 1024:
            return f"{size_bytes / 1024:.1f} KB"
        elif size_bytes < 1024 * 1024 * 1024:
            return f"{size_bytes / (1024 * 1024):.1f} MB"
        else:
            return f"{size_bytes / (1024 * 1024 * 1024):.1f} GB"
        
    def add_channel_from_folder(self, folder_name):
        """Klasör adından kanal oluştur"""
        channel_id = f"folder.{folder_name.lower().replace(' ', '_')}"
        self.channels[channel_id] = {
            'display_name': folder_name,
            'icon': ''
        }
        return channel_id
    
    def clean_filename_for_search(self, filename):
        """Dosya adını TMDB araması için temizle"""
        name = os.path.splitext(filename)[0]
        
        # Unicode karakterleri normalize et
        name = unicodedata.normalize('NFKD', name)
        
        # Yaygın kalıpları temizle
        patterns_to_remove = [
            r'\[.*?\]',  # [2023], [BluRay] gibi
            r'\(.*?\)',  # (2023), (1080p) gibi
            r'S\d+E\d+.*',  # S01E01 ve sonrası
            r'Season\s+\d+.*',  # Season 1 gibi
            r'\d{4}',    # Yıl numaraları
            r'1080p|720p|480p|4K|HDRip|BluRay|WEBRip|DVDRip|BRRip',  # Kalite
            r'x264|x265|HEVC|H\.264|H264',  # Codec
            r'YIFY|RARBG|YTS|FGT|EZTV|PublicHD',     # Release grupları
            r'HDTV|WEB-DL|WEBRip|DVDRip',  # Format
            r'AC3|DTS|AAC|MP3',  # Audio codec
            r'EXTENDED|UNRATED|DIRECTORS?\.CUT|REMASTERED',  # Sürüm
            r'MULTI|DUAL|FRENCH|GERMAN|SPANISH|ITALIAN',  # Dil
            r'COMPLETE|PROPER|REPACK|INTERNAL',  # Release info
            r'\.|-|_',   # Nokta, tire, underscore
        ]
        
        for pattern in patterns_to_remove:
            name = re.sub(pattern, ' ', name, flags=re.IGNORECASE)
        
        # Çoklu boşlukları tek boşluk yap
        name = re.sub(r'\s+', ' ', name).strip()
        
        # Çok kısa isimler için orijinal dosya adını kullan
        if len(name) < 2:
            name = os.path.splitext(filename)[0]
            name = re.sub(r'[\.\-_]', ' ', name)
            name = re.sub(r'\s+', ' ', name).strip()
        
        return name
    
    def search_tmdb_movie(self, query):
        """TMDB'de film ara"""
        try:
            url = f"{self.TMDB_BASE_URL}/search/movie"
            params = {
                'api_key': self.TMDB_API_KEY,
                'query': query,
                'language': 'en-US',  # İngilizce arama daha iyi sonuç verir
                'include_adult': False
            }
            
            response = requests.get(url, params=params)
            if response.status_code == 200:
                results = response.json().get('results', [])
                if results:
                    return results[0]
            
            # Bulunamazsa Türkçe dene
            params['language'] = 'tr-TR'
            response = requests.get(url, params=params)
            if response.status_code == 200:
                results = response.json().get('results', [])
                if results:
                    return results[0]
            
            return None
        except Exception as e:
            print(f"TMDB Movie search error: {e}")
            return None
    
    def search_tmdb_tv(self, query):
        """TMDB'de TV dizisi ara"""
        try:
            url = f"{self.TMDB_BASE_URL}/search/tv"
            params = {
                'api_key': self.TMDB_API_KEY,
                'query': query,
                'language': 'en-US',
                'include_adult': False
            }
            
            response = requests.get(url, params=params)
            if response.status_code == 200:
                results = response.json().get('results', [])
                if results:
                    return results[0]
            
            # Bulunamazsa Türkçe dene
            params['language'] = 'tr-TR'
            response = requests.get(url, params=params)
            if response.status_code == 200:
                results = response.json().get('results', [])
                if results:
                    return results[0]
            
            return None
        except Exception as e:
            print(f"TMDB TV search error: {e}")
            return None
    
    def get_tmdb_details(self, tmdb_id, media_type='movie'):
        """TMDB'den detaylı bilgi al"""
        try:
            url = f"{self.TMDB_BASE_URL}/{media_type}/{tmdb_id}"
            
            # Önce Türkçe dene
            params = {
                'api_key': self.TMDB_API_KEY,
                'language': 'tr-TR'
            }
            
            response = requests.get(url, params=params)
            if response.status_code == 200:
                tr_data = response.json()
                
                # Türkçe açıklama varsa kullan, yoksa İngilizce al
                if not tr_data.get('overview'):
                    params['language'] = 'en-US'
                    en_response = requests.get(url, params=params)
                    if en_response.status_code == 200:
                        en_data = en_response.json()
                        tr_data['overview'] = en_data.get('overview', 'Açıklama bulunamadı.')
                
                return tr_data
            return None
        except Exception as e:
            print(f"TMDB details error: {e}")
            return None
    
    def extract_video_info_with_tmdb(self, file_path, progress_callback=None, ask_user=False, 
                                    parent_window=None, default_media_type=None, default_tmdb_id=None):
        """Video dosyasından TMDB ile bilgi çıkar - İyileştirilmiş ve interaktif sürüm"""
        try:
            filename = os.path.basename(file_path)
            clean_name = self.clean_filename_for_search(filename)
            
            # Dosya boyutu ve tarihi
            size = os.path.getsize(file_path)
            mod_time = datetime.fromtimestamp(os.path.getmtime(file_path))
            
            # TMDB'den ara
            tmdb_info = None
            # Varsayılan değeri kullan, eğer belirtilmişse
            media_type = default_media_type if default_media_type else 'movie'
            search_query = clean_name
            # Varsayılan TMDB ID'si kullan, eğer belirtilmişse
            manual_tmdb_id = default_tmdb_id
            
            if progress_callback:
                progress_callback(f"TMDB bilgileri alınıyor: {filename}")
            
            # İnteraktif mod aktifse ve varsayılan değerler yoksa kullanıcıya sor
            if ask_user and parent_window and not default_tmdb_id:
                from tkinter import simpledialog
                
                # Öncelikle TMDB ID'sini sor
                tmdb_id_input = simpledialog.askstring(
                    "TMDB ID",
                    f"'{filename}' için TMDB ID'sini girin:\n"
                    f"(Bu ID'yi themoviedb.org sitesinden bulabilirsiniz)\n"
                    f"Boş bırakırsanız medya türünü seçmeniz istenecektir.",
                    parent=parent_window
                )
                
                if tmdb_id_input and tmdb_id_input.strip().isdigit():
                    manual_tmdb_id = int(tmdb_id_input.strip())
                    
                    # Medya türünü de sor (TMDB ID verildiğinde)
                    media_type_choice = simpledialog.askstring(
                        "Medya Türü",
                        f"TMDB ID: {manual_tmdb_id}\n"
                        f"Bu ID hangi türde medyaya ait?\n"
                        "1. Film\n"
                        "2. TV Dizisi\n"
                        "Seçiminizi yapın (1/2):",
                        parent=parent_window
                    )
                    
                    if media_type_choice == "1":
                        media_type = "movie" 
                    elif media_type_choice == "2":
                        media_type = "tv"
                        
                    if progress_callback:
                        progress_callback(f"Manuel TMDB ID kullanılıyor: {manual_tmdb_id} ({media_type})")
                else:
                    # TMDB ID verilmediyse medya türünü sor
                    media_type_choice = simpledialog.askstring(
                        "İçerik Türü",
                        f"'{filename}' için içerik türünü seçin:\n"
                        "1. Film\n"
                        "2. TV Dizisi\n"
                        "Seçiminizi yapın (1/2) veya boş bırakın (otomatik):",
                        parent=parent_window
                    )
                    
                    if media_type_choice == "1":
                        media_type = "movie"
                    elif media_type_choice == "2":
                        media_type = "tv"
        
        # Manuel TMDB ID verilmişse, doğrudan API'yi kullan
        if manual_tmdb_id:
            try:
                tmdb_info = self.get_tmdb_details(manual_tmdb_id, media_type)
                if tmdb_info:
                    if progress_callback:
                        title = tmdb_info.get('title', tmdb_info.get('name', 'N/A'))
                        progress_callback(f"✅ TMDB ID ile bulundu: {title} ({media_type})")
                else:
                    if progress_callback:
                        progress_callback(f"❌ TMDB ID ile bilgi bulunamadı: {manual_tmdb_id} ({media_type})")
            except Exception as e:
                if progress_callback:
                    progress_callback(f"❌ TMDB ID ile sorgulama hatası: {str(e)}")
                logger.error(f"TMDB ID sorgusu hatası: {str(e)}")
        # Manuel ID yoksa otomatik arama yap
        elif len(search_query.replace(' ', '')) >= 3:
            try:
                if media_type == 'movie':
                    # Film olarak ara
                    movie_result = self.search_tmdb_movie(search_query)
                    if movie_result:
                        tmdb_info = self.get_tmdb_details(movie_result['id'], 'movie')
                        if progress_callback:
                            progress_callback(f"✅ Film bulundu: {movie_result.get('title', 'N/A')}")
                    elif not ask_user:  # Kullanıcı sormadıysak TV olarak da ara
                        # Film bulunamazsa TV dizisi olarak ara
                        tv_result = self.search_tmdb_tv(search_query)
                        if tv_result:
                            tmdb_info = self.get_tmdb_details(tv_result['id'], 'tv')
                            media_type = 'tv'
                            if progress_callback:
                                progress_callback(f"✅ Dizi bulundu: {tv_result.get('name', 'N/A')}")
                        else:
                            if progress_callback:
                                progress_callback(f"❌ TMDB'de bulunamadı: {search_query}")
                else:  # media_type == 'tv'
                    # TV dizisi olarak ara
                    tv_result = self.search_tmdb_tv(search_query)
                    if tv_result:
                        tmdb_info = self.get_tmdb_details(tv_result['id'], 'tv')
                        if progress_callback:
                            progress_callback(f"✅ Dizi bulundu: {tv_result.get('name', 'N/A')}")
                    else:
                        if progress_callback:
                            progress_callback(f"❌ TMDB'de bulunamadı: {search_query}")
            except Exception as e:
                if progress_callback:
                    progress_callback(f"❌ TMDB arama hatası: {str(e)}")
                logger.error(f"TMDB arama hatası: {str(e)}")
        else:
            if progress_callback:
                progress_callback(f"❌ Çok kısa arama terimi: {search_query}")
            
        # API rate limiting için bekleme
        time.sleep(0.3)
        
        if tmdb_info:
            # TMDB bilgilerini kullan
            if media_type == 'movie':
                title = tmdb_info.get('title', clean_name)
                description = tmdb_info.get('overview', 'Açıklama bulunamadı.')
                release_date = tmdb_info.get('release_date', '')
                genres = [g['name'] for g in tmdb_info.get('genres', [])] if 'genres' in tmdb_info else []
                runtime = tmdb_info.get('runtime', 120)  # dakika
                rating = tmdb_info.get('vote_average', 0)
                poster_path = tmdb_info.get('poster_path', '')
                original_title = tmdb_info.get('original_title', title)
                tmdb_id = tmdb_info.get('id', '')
            else:  # TV
                title = tmdb_info.get('name', clean_name)
                description = tmdb_info.get('overview', 'Açıklama bulunamadı.')
                release_date = tmdb_info.get('first_air_date', '')
                genres = [g['name'] for g in tmdb_info.get('genres', [])] if 'genres' in tmdb_info else []
                runtime = tmdb_info.get('episode_run_time', [45])[0] if tmdb_info.get('episode_run_time') else 45
                rating = tmdb_info.get('vote_average', 0)
                poster_path = tmdb_info.get('poster_path', '')
                original_title = tmdb_info.get('original_name', title)
                tmdb_id = tmdb_info.get('id', '')
            
            # Süreyi saniyeye çevir
            duration_seconds = runtime * 60
            
            # Açıklamayı zenginleştir
            enhanced_description = f"{description}\n\n"
            if original_title != title:
                enhanced_description += f"🎬 Orijinal Adı: {original_title}\n"
            enhanced_description += f"📊 TMDB ID: {tmdb_id}\n"
            if release_date:
                enhanced_description += f"📅 Yayın Tarihi: {release_date}\n"
            if genres:
                enhanced_description += f"🎭 Türler: {', '.join(genres)}\n"
            if rating > 0:
                enhanced_description += f"⭐ TMDB Puanı: {rating:.1f}/10\n"
            enhanced_description += f"💾 Dosya Boyutu: {self.format_size(size)}\n"
            enhanced_description += f"📁 Dosya: {filename}\n"
            enhanced_description += f"🔍 Arama Terimi: {search_query}"
            
            poster_url = f"https://image.tmdb.org/t/p/w500{poster_path}" if poster_path else ""
            
        else:
            # TMDB'de bulunamadı, temel bilgileri kullan
            title = clean_name if clean_name.strip() else filename
            duration_seconds = max(3600, size / (1024 * 1024 * 1024) * 3600)  # Tahmini süre
            enhanced_description = f"🎬 Başlık: {title}\n"
            enhanced_description += f"📁 Dosya: {filename}\n"
            enhanced_description += f"💾 Dosya Boyutu: {self.format_size(size)}\n"
            enhanced_description += f"🔍 Arama Terimi: {search_query}\n"
            enhanced_description += f"⚠ TMDB'de bulunamadı - Manuel olarak kontrol edin"
            genres = ['Bilinmiyor']
            rating = 0
            poster_url = ""
            original_title = title
            tmdb_id = ""
        
        return {
            'filename': filename,
            'title': title,
            'original_title': original_title if tmdb_info else title,
            'description': enhanced_description,
            'duration': int(duration_seconds),
            'size': size,
            'modified': mod_time,
            'path': file_path,
            'tmdb_found': tmdb_info is not None,
            'media_type': media_type,
            'genres': genres,
            'rating': rating,
            'poster_url': poster_url,
            'clean_search_name': search_query,
            'tmdb_id': tmdb_id
        }
        except Exception as e:
            logger.error(f"Video bilgisi çıkarma hatası: {str(e)}", exc_info=True)
            if progress_callback:
                progress_callback(f"❌ Hata oluştu: {str(e)}")
            
            # Hata durumunda en azından temel bilgileri döndür
            filename = os.path.basename(file_path)
            return {
                'filename': filename,
                'title': filename,
                'original_title': filename,
                'description': f"Video bilgisi alınamadı: {filename}",
                'duration': 3600,  # Varsayılan 1 saat
                'size': os.path.getsize(file_path) if os.path.exists(file_path) else 0,
                'modified': datetime.now(),
                'path': file_path,
                'tmdb_found': False,
                'media_type': 'unknown',
                'genres': ['Bilinmiyor'],
                'rating': 0,
                'poster_url': "",
                'clean_search_name': filename,
                'tmdb_id': ""
            }
    
    def search_tmdb_movie(self, query):
        """TMDB'de film ara"""
        try:
            url = f"{self.TMDB_BASE_URL}/search/movie"
            params = {
                'api_key': self.TMDB_API_KEY,
                'query': query,
                'language': 'en-US',  # İngilizce arama daha iyi sonuç verir
                'include_adult': False
            }
            
            response = requests.get(url, params=params)
            if response.status_code == 200:
                results = response.json().get('results', [])
                if results:
                    return results[0]
            
            # Bulunamazsa Türkçe dene
            params['language'] = 'tr-TR'
            response = requests.get(url, params=params)
            if response.status_code == 200:
                results = response.json().get('results', [])
                if results:
                    return results[0]
            
            return None
        except Exception as e:
            print(f"TMDB Movie search error: {e}")
            return None
    
    def search_tmdb_tv(self, query):
        """TMDB'de TV dizisi ara"""
        try:
            url = f"{self.TMDB_BASE_URL}/search/tv"
            params = {
                'api_key': self.TMDB_API_KEY,
                'query': query,
                'language': 'en-US',
                'include_adult': False
            }
            
            response = requests.get(url, params=params)
            if response.status_code == 200:
                results = response.json().get('results', [])
                if results:
                    return results[0]
            
            # Bulunamazsa Türkçe dene
            params['language'] = 'tr-TR'
            response = requests.get(url, params=params)
            if response.status_code == 200:
                results = response.json().get('results', [])
                if results:
                    return results[0]
            
            return None
        except Exception as e:
            print(f"TMDB TV search error: {e}")
            return None
    
    def get_tmdb_details(self, tmdb_id, media_type='movie'):
        """TMDB'den detaylı bilgi al"""
        try:
            url = f"{self.TMDB_BASE_URL}/{media_type}/{tmdb_id}"
            
            # Önce Türkçe dene
            params = {
                'api_key': self.TMDB_API_KEY,
                'language': 'tr-TR'
            }
            
            response = requests.get(url, params=params)
            if response.status_code == 200:
                tr_data = response.json()
                
                # Türkçe açıklama varsa kullan, yoksa İngilizce al
                if not tr_data.get('overview'):
                    params['language'] = 'en-US'
                    en_response = requests.get(url, params=params)
                    if en_response.status_code == 200:
                        en_data = en_response.json()
                        tr_data['overview'] = en_data.get('overview', 'Açıklama bulunamadı.')
                
                return tr_data
            return None
        except Exception as e:
            print(f"TMDB details error: {e}")
            return None
    
    def generate_epg_from_videos(self, video_files_by_folder, days=7):
        """Video dosyalarından EPG oluştur"""
        root = ET.Element('tv', generator_name="TMDB Video EPG Generator")
        
        # Kanalları ekle
        for folder_name, channel_info in self.channels.items():
            channel_elem = ET.SubElement(root, 'channel', id=folder_name)
            display_name = ET.SubElement(channel_elem, 'display-name')
            display_name.text = channel_info['display_name']
            
            # Kanal açıklaması
            desc = ET.SubElement(channel_elem, 'desc')
            desc.text = f"Video klasörü: {channel_info['display_name']}"
        
        # Her klasör için programları ekle
        for folder_name, video_files in video_files_by_folder.items():
            if folder_name not in self.channels:
                continue
                
            current_time = datetime.now().replace(hour=6, minute=0, second=0, microsecond=0)
            
            for day in range(days):
                day_start = current_time + timedelta(days=day)
                time_cursor = day_start
                
                # O gün için video dosyalarını sırayla yerleştir
                for video_info in video_files:
                    # Gün sona erdiyse sonraki güne geç
                    next_day = day_start + timedelta(days=1)
                    if time_cursor >= next_day:
                        break
                    
                    # Program süresi
                    duration = max(1800, min(video_info['duration'], 14400))
                    
                    programme = ET.SubElement(root, 'programme',
                                            channel=folder_name,
                                            start=time_cursor.strftime('%Y%m%d%H%M%S +0000'),
                                            stop=(time_cursor + timedelta(seconds=duration)).strftime('%Y%m%d%H%M%S +0000'))
                    
                    # Başlık
                    title = ET.SubElement(programme, 'title', lang='tr')
                    title.text = video_info['title']
                    
                    # Açıklama
                    desc = ET.SubElement(programme, 'desc', lang='tr')
                    desc.text = video_info['description']
                    
                    # Kategori
                    category = ET.SubElement(programme, 'category', lang='tr')
                    if video_info.get('genres'):
                        category.text = video_info['genres'][0]
                    else:
                        category.text = 'Film' if video_info.get('media_type') == 'movie' else 'Dizi'
                    
                    # Süre
                    length = ET.SubElement(programme, 'length', units='seconds')
                    length.text = str(duration)
                    
                    # Rating varsa ekle
                    if video_info.get('rating', 0) > 0:
                        rating = ET.SubElement(programme, 'star-rating')
                        value = ET.SubElement(rating, 'value')
                        value.text = f"{video_info['rating']:.1f}/10"
                    
                    # Poster varsa ekle
                    if video_info.get('poster_url'):
                        icon = ET.SubElement(programme, 'icon', src=video_info['poster_url'])
                    
                    # Credits (TMDB kaynak bilgisi)
                    if video_info.get('tmdb_found'):
                        credits = ET.SubElement(programme, 'credits')
                        adapter = ET.SubElement(credits, 'adapter')
                        adapter.text = "TMDB (The Movie Database)"
                    
                    # Bir sonraki programın başlangıç zamanı
                    time_cursor += timedelta(seconds=duration)
                    
                    # Programlar arası 5 dakika reklam/tanıtım
                    time_cursor += timedelta(minutes=5)
        
        return ET.tostring(root, encoding='unicode')
    
    def save_epg(self, filename, content):
        """EPG'yi dosyaya kaydet"""
        directory = os.path.dirname(filename)
        if directory and not os.path.exists(directory):
            os.makedirs(directory, exist_ok=True)
        
        with open(filename, 'w', encoding='utf-8') as f:
            f.write(content)

class IPTVManagerApp:
    def __init__(self, root):
        self.root = root
        self.root.title("IPTV Kanal Yönetim Sistemi")
        self.root.geometry("900x650")
        self.root.minsize(800, 600)
        logger.info("GUI başlatılıyor")
        
        # Uygulama ikonu ekle (varsa)
        icon_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "icon.ico")
        if os.path.exists(icon_path):
            self.root.iconbitmap(icon_path)
        
        self.config_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "channels_config.json")
        self.output_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "hls")
        self.http_port = 8080
        self.http_server_thread = None
        self.ffmpeg_processes = {}
        
        # Varsayılan ayarlar
        self.channels = []
        self.temp_folders = []  # Geçici klasör listesi
        self.editing_index = -1  # Düzenlenen kanal indeksi
        self.autostart = False   # Otomatik başlatma ayarı
        
        # HTTP sunucusunu başlatma kontrolü - widget'lardan önce tanımlanmalı
        self.server_running = False
        
        # TMDB EPG Generator instance
        self.epg_generator = TMDBVideoEPGGenerator()
        self.epg_folders = []  # EPG için seçilen klasörler
        self.progress_text = None  # İlerleme durumu için metin alanı
        
        # Yapılandırma dosyasını yükle
        self.load_config()
        
        # Menü oluştur
        self.create_menu()
        
        # Ana çerçeveyi oluştur
        self.create_widgets()
        
        # Başlangıç mesajı
        self.status_var.set("IPTV Kanal Yönetim Sistemi başlatıldı")
        
        # Otomatik başlatma özelliği
        if self.autostart:
            self.root.after(1000, self.auto_start_server)
    
    def create_menu(self):
        """Menü çubuğu oluştur"""
        menubar = tk.Menu(self.root)
        self.root.config(menu=menubar)
        
        # Dosya menüsü
        file_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Dosya", menu=file_menu)
        file_menu.add_command(label="M3U Listesi Oluştur", command=self.create_m3u_playlist)
        file_menu.add_command(label="Hata Günlüğünü Görüntüle", command=self.show_error_log)
        file_menu.add_separator()
        file_menu.add_command(label="Çıkış", command=self.root.quit)
        
        # Sunucu menüsü
        server_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Sunucu", menu=server_menu)
        server_menu.add_command(label="HTTP Sunucusu Başlat", command=self.start_http_server)
        server_menu.add_command(label="HTTP Sunucusu Durdur", command=self.stop_http_server)
        server_menu.add_separator()
        server_menu.add_command(label="Tüm Kanalları Başlat", command=self.start_all_channels)
        server_menu.add_command(label="Tüm Kanalları Durdur", command=self.stop_all_channels)
        
        # Ayarlar menüsü
        settings_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Ayarlar", menu=settings_menu)
        
        # Otomatik başlatma seçeneği
        self.autostart_var = tk.BooleanVar(value=self.autostart)
        settings_menu.add_checkbutton(label="Otomatik Başlat", variable=self.autostart_var, 
                                      command=self.toggle_autostart)
        
        # Yardım menüsü
        help_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Yardım", menu=help_menu)
        help_menu.add_command(label="Kullanım Kılavuzu", command=self.show_help)
        help_menu.add_command(label="Hakkında", command=self.show_about)
    
    def create_widgets(self):
        # Ana sekme yapısını oluştur
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill="both", expand=True, padx=5, pady=5)
        
        # Kanal Yönetimi sekmesi
        self.channel_tab = ttk.Frame(self.notebook)
        self.notebook.add(self.channel_tab, text="Kanal Yönetimi")
        
        # EPG Oluşturma sekmesi
        self.epg_tab = ttk.Frame(self.notebook)
        self.notebook.add(self.epg_tab, text="EPG Oluşturma")
        
        # Kanal sekmesi için widget'ları oluştur
        self.create_channel_tab_widgets()
        
        # EPG sekmesi için widget'ları oluştur
        self.create_epg_tab_widgets()
        
        # Durum çubuğu
        self.status_var = tk.StringVar()
        self.status_var.set("Hazır")
        status_bar = ttk.Label(self.root, textvariable=self.status_var, relief="sunken", anchor="w")
        status_bar.pack(side="bottom", fill="x")
        
        # Kanalları listeye ekle
        self.refresh_channel_list()
        
        # Sunucu durumuna göre butonları güncelle
        self.update_server_buttons()
    
    def create_channel_tab_widgets(self):
        """Kanal sekmesi için widget'ları oluştur"""
        # Ana çerçeveleri oluştur
        top_frame = ttk.Frame(self.channel_tab)
        top_frame.pack(fill="x", padx=10, pady=5)
        
        # Hızlı erişim butonları - genişletilmiş çerçeve
        quick_access_frame = ttk.LabelFrame(top_frame, text="Hızlı Erişim")
        quick_access_frame.pack(fill="x", padx=5, pady=5, expand=True)
        
        # Butonlar için grid layout kullan
        btn_frame = ttk.Frame(quick_access_frame)
        btn_frame.pack(fill="x", padx=5, pady=5)
        
        # Grid yapısını yapılandır - genişleme için
        btn_frame.columnconfigure(0, weight=1)
        btn_frame.columnconfigure(1, weight=1)
        btn_frame.columnconfigure(2, weight=1)
        btn_frame.columnconfigure(3, weight=1)
        
        self.start_server_btn = ttk.Button(btn_frame, text="HTTP Sunucusu Başlat", 
                                           command=self.start_http_server, width=25)
        self.start_server_btn.grid(row=0, column=0, padx=5, pady=5, sticky="ew")
        
        self.stop_server_btn = ttk.Button(btn_frame, text="HTTP Sunucusu Durdur", 
                                          command=self.stop_http_server, width=25)
        self.stop_server_btn.grid(row=0, column=1, padx=5, pady=5, sticky="ew")
        
        self.start_channels_btn = ttk.Button(btn_frame, text="Tüm Kanalları Başlat", 
                                             command=self.start_all_channels, width=25)
        self.start_channels_btn.grid(row=0, column=2, padx=5, pady=5, sticky="ew")
        
        self.stop_channels_btn = ttk.Button(btn_frame, text="Tüm Kanalları Durdur", 
                                            command=self.stop_all_channels, width=25)
        self.stop_channels_btn.grid(row=0, column=3, padx=5, pady=5, sticky="ew")
        
        self.create_m3u_btn = ttk.Button(btn_frame, text="M3U Listesi Oluştur", 
                                         command=self.create_m3u_playlist, width=25)
        self.create_m3u_btn.grid(row=1, column=0, padx=5, pady=5, sticky="ew")
        
        self.help_btn = ttk.Button(btn_frame, text="Yardım", 
                                   command=self.show_help, width=25)
        self.help_btn.grid(row=1, column=1, padx=5, pady=5, sticky="ew")
        
        # Ana çerçeveleri oluştur - genişletilmiş
        channel_frame = ttk.LabelFrame(self.channel_tab, text="Kanal Listesi")
        channel_frame.pack(fill="both", expand=True, padx=10, pady=5)
        
        control_frame = ttk.LabelFrame(self.channel_tab, text="Kontrol Paneli")
        control_frame.pack(fill="both", expand=False, padx=10, pady=5)
        
        # Kanal listesi için treeview
        self.channel_tree = ttk.Treeview(channel_frame, columns=("name", "folder_count", "port", "status"), show="headings")
        self.channel_tree.heading("name", text="Kanal Adı")
        self.channel_tree.heading("folder_count", text="Klasör Sayısı")
        self.channel_tree.heading("port", text="Port")
        self.channel_tree.heading("status", text="Durum")
        
        self.channel_tree.column("name", width=150)
        self.channel_tree.column("folder_count", width=80)
        self.channel_tree.column("port", width=80)
        self.channel_tree.column("status", width=100)
        
        # Scrollbar ekle
        scrollbar = ttk.Scrollbar(channel_frame, orient="vertical", command=self.channel_tree.yview)
        self.channel_tree.configure(yscrollcommand=scrollbar.set)
        
        self.channel_tree.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        
        # Kanal düzenleme çerçevesi
        edit_frame = ttk.LabelFrame(control_frame, text="Kanal Bilgisi")
        edit_frame.pack(fill="both", expand=True, padx=10, pady=5)
        
        ttk.Label(edit_frame, text="Kanal Adı:").grid(row=0, column=0, sticky="w", padx=5, pady=5)
        self.name_entry = ttk.Entry(edit_frame, width=30)
        self.name_entry.grid(row=0, column=1, columnspan=2, sticky="ew", padx=5, pady=5)
        
        ttk.Label(edit_frame, text="Port:").grid(row=1, column=0, sticky="w", padx=5, pady=5)
        self.port_entry = ttk.Entry(edit_frame, width=10)
        self.port_entry.grid(row=1, column=1, sticky="w", padx=5, pady=5)
        # Otomatik port ata butonu ekle
        self.auto_port_btn = ttk.Button(edit_frame, text="Otomatik Port", command=self.auto_assign_port, width=12)
        self.auto_port_btn.grid(row=1, column=2, sticky="w", padx=5, pady=5)
        
        # Logo alanı
        ttk.Label(edit_frame, text="Kanal Logosu:").grid(row=2, column=0, sticky="w", padx=5, pady=5)
        
        # Logo için frame oluştur
        logo_frame = ttk.Frame(edit_frame)
        logo_frame.grid(row=2, column=1, columnspan=2, sticky="ew", padx=5, pady=5)
        logo_frame.columnconfigure(0, weight=1)
        
        # Logo URL/Dosya yolu entry
        self.logo_entry = ttk.Entry(logo_frame, width=35)
        self.logo_entry.grid(row=0, column=0, sticky="ew", padx=(0, 5))
        
        # Logo seçme butonları
        logo_btn_frame = ttk.Frame(logo_frame)
        logo_btn_frame.grid(row=0, column=1, sticky="e")
        
        self.logo_file_btn = ttk.Button(logo_btn_frame, text="📁 Dosya", command=self.select_logo_file, width=8)
        self.logo_file_btn.pack(side="left", padx=2)
        
        self.logo_clear_btn = ttk.Button(logo_btn_frame, text="🗑️ Temizle", command=self.clear_logo, width=8)
        self.logo_clear_btn.pack(side="left", padx=2)
        
        # EPG alanı
        ttk.Label(edit_frame, text="EPG URL:").grid(row=3, column=0, sticky="w", padx=5, pady=5)
        
        # EPG için frame oluştur
        epg_frame = ttk.Frame(edit_frame)
        epg_frame.grid(row=3, column=1, columnspan=2, sticky="ew", padx=5, pady=5)
        epg_frame.columnconfigure(0, weight=1)
        
        # EPG URL entry
        self.epg_url_entry = ttk.Entry(epg_frame, width=35)
        self.epg_url_entry.grid(row=0, column=0, sticky="ew", padx=(0, 5))
        
        # EPG seçme butonları
        epg_btn_frame = ttk.Frame(epg_frame)
        epg_btn_frame.grid(row=0, column=1, sticky="e")
        
        self.epg_file_btn = ttk.Button(epg_btn_frame, text="📄 Dosya", command=self.select_epg_file, width=8)
        self.epg_file_btn.pack(side="left", padx=2)
        
        self.epg_clear_btn = ttk.Button(epg_btn_frame, text="🗑️ Temizle", command=self.clear_epg, width=8)
        self.epg_clear_btn.pack(side="left", padx=2)
        
        # EPG ID alanı
        ttk.Label(edit_frame, text="EPG ID:").grid(row=4, column=0, sticky="w", padx=5, pady=5)
        self.epg_id_entry = ttk.Entry(edit_frame, width=30)
        self.epg_id_entry.grid(row=4, column=1, columnspan=2, sticky="ew", padx=5, pady=5)
        
        # Klasör listesi çerçevesi
        folder_frame = ttk.LabelFrame(control_frame, text="Klasör Listesi")
        folder_frame.pack(fill="both", expand=True, padx=10, pady=5)
        
        # Klasör listesi için treeview
        self.folder_tree = ttk.Treeview(folder_frame, columns=("path"), show="headings")
        self.folder_tree.heading("path", text="Klasör Yolu")
        self.folder_tree.column("path", width=600)
        
        # Scrollbar ekle
        folder_scrollbar = ttk.Scrollbar(folder_frame, orient="vertical", command=self.folder_tree.yview)
        self.folder_tree.configure(yscrollcommand=folder_scrollbar.set)
        
        self.folder_tree.pack(side="left", fill="both", expand=True)
        folder_scrollbar.pack(side="right", fill="y")
        
        # Klasör işlem butonları
        folder_btn_frame = ttk.Frame(folder_frame)
        folder_btn_frame.pack(fill="x", padx=5, pady=5)
        
        # Ana klasör işlem butonları bir satırda
        self.add_folder_btn = ttk.Button(folder_btn_frame, text="Klasör Ekle", command=self.add_folder, width=15)
        self.add_folder_btn.pack(side="left", padx=5)
        
        self.remove_folder_btn = ttk.Button(folder_btn_frame, text="Seçili Klasörü Sil", command=self.remove_folder, width=15)
        self.remove_folder_btn.pack(side="left", padx=5)
        
        # Klasörleri kanala ekleme butonu
        folder_action_frame = ttk.Frame(folder_frame)
        folder_action_frame.pack(fill="x", padx=5, pady=10)
        
        self.assign_folders_btn = ttk.Button(
            folder_action_frame, 
            text="➡️ KLASÖRLER HAZIR - KANAL EKLE ➡️", 
            command=self.assign_folders_to_channel,
            style="Action.TButton",
            width=40
        )
        self.assign_folders_btn.pack(side="top", fill="x", padx=5, pady=5)
        
        # Özel stil tanımla
        style = ttk.Style()
        style.configure("Action.TButton", font=("Arial", 10, "bold"))
        
        # Kanal işlem butonları
        btn_frame = ttk.Frame(control_frame)
        btn_frame.pack(fill="x", padx=10, pady=10)
        
        # Grid yapısını yapılandır - butonlar için genişleme sağlar
        btn_frame.columnconfigure(0, weight=1)
        btn_frame.columnconfigure(1, weight=1)
        btn_frame.columnconfigure(2, weight=1)
        btn_frame.columnconfigure(3, weight=1)
        btn_frame.columnconfigure(4, weight=1)
        
        # Kanal ekle butonu - büyük ve belirgin
        self.add_btn = ttk.Button(btn_frame, text="KANAL EKLE", 
                                  command=self.add_channel, 
                                  width=25,
                                  style="Action.TButton")
        self.add_btn.grid(row=0, column=0, padx=5, pady=5, sticky="ew")
        
        # Diğer butonlar - genişletilmiş
        self.update_btn = ttk.Button(btn_frame, text="Kanal Güncelle", 
                                    command=self.update_channel, 
                                    state="disabled", width=20)
        self.update_btn.grid(row=0, column=1, padx=5, pady=5, sticky="ew")
        
        self.delete_btn = ttk.Button(btn_frame, text="Kanal Sil", 
                                    command=self.delete_channel, 
                                    state="disabled", width=20)
        self.delete_btn.grid(row=0, column=2, padx=5, pady=5, sticky="ew")
        
        self.start_channel_btn = ttk.Button(btn_frame, text="Seçili Kanalı Başlat", 
                                          command=self.start_selected_channel, 
                                          state="disabled", width=20)
        self.start_channel_btn.grid(row=0, column=3, padx=5, pady=5, sticky="ew")
        
        self.stop_channel_btn = ttk.Button(btn_frame, text="Seçili Kanalı Durdur", 
                                         command=self.stop_selected_channel, 
                                         state="disabled", width=20)
        self.stop_channel_btn.grid(row=0, column=4, padx=5, pady=5, sticky="ew")
        
        # Kanal seçme olayını bağla
        self.channel_tree.bind("<<TreeviewSelect>>", self.on_channel_select)

    def create_epg_tab_widgets(self):
        """EPG oluşturma sekmesi için widget'ları oluştur"""
        # Ana çerçeve
        main_frame = ttk.Frame(self.epg_tab)
        main_frame.pack(fill="both", expand=True, padx=10, pady=10)
        
        # EPG oluşturma seçenekleri
        options_frame = ttk.LabelFrame(main_frame, text="EPG Oluşturma Seçenekleri")
        options_frame.pack(fill="x", padx=5, pady=5)
        
        # İçerik çerçevesi
        options_content = ttk.Frame(options_frame)
        options_content.pack(fill="x", padx=10, pady=10)
        
        # EPG Dosyası adı
        ttk.Label(options_content, text="EPG Dosyası:").grid(row=0, column=0, sticky="w", padx=5, pady=5)
        self.epg_filename_var = tk.StringVar(value="video_epg.xml")
        self.epg_filename_entry = ttk.Entry(options_content, textvariable=self.epg_filename_var, width=30)
        self.epg_filename_entry.grid(row=0, column=1, sticky="w", padx=5, pady=5)
        
        # EPG gün sayısı
        ttk.Label(options_content, text="Gün Sayısı:").grid(row=0, column=2, sticky="w", padx=5, pady=5)
        self.epg_days_var = tk.IntVar(value=7)
        self.epg_days_spinbox = ttk.Spinbox(options_content, from_=1, to=14, textvariable=self.epg_days_var, width=5)
        self.epg_days_spinbox.grid(row=0, column=3, sticky="w", padx=5, pady=5)
        
        # Klasör listesi çerçevesi
        video_frame = ttk.LabelFrame(main_frame, text="Video Klasörleri")
        video_frame.pack(fill="both", expand=True, padx=5, pady=5)
        
        # Klasör listesi için treeview
        self.epg_folder_tree = ttk.Treeview(video_frame, columns=("path", "status"), show="headings")
        self.epg_folder_tree.heading("path", text="Klasör Yolu")
        self.epg_folder_tree.heading("status", text="Durum")
        
        self.epg_folder_tree.column("path", width=500)
        self.epg_folder_tree.column("status", width=100)
        
        # Scrollbar ekle
        epg_folder_scrollbar = ttk.Scrollbar(video_frame, orient="vertical", command=self.epg_folder_tree.yview)
        self.epg_folder_tree.configure(yscrollcommand=epg_folder_scrollbar.set)
        
        self.epg_folder_tree.pack(side="left", fill="both", expand=True)
        epg_folder_scrollbar.pack(side="right", fill="y")
        
        # Klasör işlem butonları
        epg_folder_btn_frame = ttk.Frame(video_frame)
        epg_folder_btn_frame.pack(fill="x", padx=5, pady=5)
        
        self.add_epg_folder_btn = ttk.Button(epg_folder_btn_frame, text="Klasör Ekle", 
                                             command=self.add_epg_folder, width=15)
        self.add_epg_folder_btn.pack(side="left", padx=5)
        
        self.remove_epg_folder_btn = ttk.Button(epg_folder_btn_frame, text="Seçili Klasörü Sil", 
                                                command=self.remove_epg_folder, width=15)
        self.remove_epg_folder_btn.pack(side="left", padx=5)
        
        # İlerleme durumu için metin alanı
        progress_frame = ttk.LabelFrame(main_frame, text="İşlem Durumu")
        progress_frame.pack(fill="both", expand=True, padx=5, pady=5)
        
        # Scrollable text widget
        self.progress_text = scrolledtext.ScrolledText(progress_frame, height=10)
        self.progress_text.pack(fill="both", expand=True, padx=5, pady=5)
        
        # EPG oluşturma butonu
        btn_frame = ttk.Frame(main_frame)
        btn_frame.pack(fill="x", padx=5, pady=10)
        
        self.generate_epg_btn = ttk.Button(btn_frame, text="EPG OLUŞTUR", 
                                          command=self.generate_epg,
                                          width=30, 
                                          style="Action.TButton")
        self.generate_epg_btn.pack(side="left", padx=5)
        
        self.view_epg_btn = ttk.Button(btn_frame, text="EPG Dosyasını Görüntüle", 
                                       command=self.view_epg_file,
                                       width=20)
        self.view_epg_btn.pack(side="left", padx=5)
        
        self.clear_progress_btn = ttk.Button(btn_frame, text="Durumu Temizle", 
                                            command=lambda: self.progress_text.delete(1.0, tk.END),
                                            width=15)
        self.clear_progress_btn.pack(side="left", padx=5)

    # EPG ile ilgili yeni metotlar
    def add_epg_folder(self):
        """EPG için klasör seç"""
        folder_path = filedialog.askdirectory(title="Video Klasörünü Seçin")
        if folder_path:
            # Aynı klasör zaten var mı kontrol et
            if folder_path in self.epg_folders:
                messagebox.showwarning("Uyarı", "Bu klasör zaten eklenmiş!")
                return
            
            # Klasörü ekle
            self.epg_folders.append(folder_path)
            
            # Treeview'a ekle
            self.epg_folder_tree.insert("", "end", values=(folder_path, "Hazır"))
            
            # Kullanıcıya bildir
            self.log_progress(f"Video klasörü eklendi: {folder_path}")
            
    def remove_epg_folder(self):
        """Seçili EPG klasörünü listeden kaldır"""
        selected = self.epg_folder_tree.selection()
        if not selected:
            messagebox.showwarning("Uyarı", "Lütfen silinecek bir klasör seçin!")
            return
        
        # Seçili klasörü bul ve kaldır
        for item in selected:
            path = self.epg_folder_tree.item(item, 'values')[0]
            if path in self.epg_folders:
                self.epg_folders.remove(path)
            self.epg_folder_tree.delete(item)
        
        self.log_progress(f"Klasör listeden kaldırıldı")
    
    def log_progress(self, message):
        """İlerleme durumunu log kaydet ve göster"""
        timestamp = datetime.now().strftime('%H:%M:%S')
        log_entry = f"[{timestamp}] {message}\n"
        
        # Metin alanına ekle
        if self.progress_text:
            self.progress_text.insert(tk.END, log_entry)
            self.progress_text.see(tk.END)  # Otomatik kaydır
            self.progress_text.update()  # UI güncelle
        
        # Konsola ve log dosyasına da yaz
        logger.info(message)
    
    def generate_epg(self):
        """EPG oluşturma işlemi"""
        if not self.epg_folders:
            messagebox.showwarning("Uyarı", "En az bir video klasörü eklemelisiniz!")
            return
        
        # EPG dosya adını al
        epg_filename = self.epg_filename_var.get().strip()
        if not epg_filename:
            epg_filename = "video_epg.xml"
            self.epg_filename_var.set(epg_filename)
        
        # .xml uzantısını kontrol et
        if not epg_filename.endswith(".xml"):
            epg_filename += ".xml"
            self.epg_filename_var.set(epg_filename)
        
        # Gün sayısını al
        days = self.epg_days_var.get()
        if days < 1:
            days = 7
            self.epg_days_var.set(days)
        
        # EPG oluşturma işlemini başka bir thread'de çalıştır
        self.log_progress(f"EPG oluşturma işlemi başlatılıyor... ({len(self.epg_folders)} klasör, {days} gün)")
        self.generate_epg_btn.config(state="disabled")
        
        thread = threading.Thread(target=self._generate_epg_thread, args=(epg_filename, days), daemon=True)
        thread.start()
    
    def _generate_epg_thread(self, epg_filename, days):
        """EPG oluşturma işlemi (Thread içinde) - İyileştirilmiş ve interaktif sürüm"""
        try:
            # İlerleme durumu
            self.log_progress("EPG için video klasörleri taranıyor...")
            
            # Kullanıcıdan manuel bilgi girişi yapılsın mı sorusu
            ask_user_input = messagebox.askyesno(
                "Manuel Bilgi Girişi", 
                "Video bilgilerini otomatik çekmeden önce size sorulsun mu?\n\n"
                "Evet: Her klasör için TMDB ID'sini girebilirsiniz.\n"
                "Hayır: Tüm bilgiler otomatik olarak çekilecektir."
            )
            
            # İşlemi başlat
            epg_generator = self.epg_generator
            epg_generator.channels = {}  # Önceki kanalları temizle
            
            # Her klasör için
            video_files_by_folder = {}
            
            for folder_path in self.epg_folders:
                try:
                    self.log_progress(f"Klasör taranıyor: {folder_path}")
                    folder_name = os.path.basename(folder_path)
                    
                    # Kanal oluştur
                    channel_id = epg_generator.add_channel_from_folder(folder_name)
                    
                    # Video dosyalarını bul
                    video_files = []
                    for ext in [".mp4", ".mkv", ".avi", ".mov", ".wmv"]:
                        try:
                            video_files.extend(glob.glob(os.path.join(folder_path, "*" + ext)))
                            # Alt klasörleri de tara
                            video_files.extend(glob.glob(os.path.join(folder_path, "**", "*" + ext), recursive=True))
                        except Exception as e:
                            self.log_progress(f"Uzantı tarama hatası {ext}: {str(e)}")
                    
                    # Dosyaları sırala (alfabetik)
                    video_files.sort()
                    
                    self.log_progress(f"'{folder_name}' klasöründe {len(video_files)} video dosyası bulundu")
                    
                    # Eğer kullanıcı girişi isteniyorsa, klasör başına tek sefer sor
                    folder_media_type = None  # Başlangıçta medya türü belirtilmemiş
                    folder_tmdb_id = None
                    
                    if ask_user_input and video_files:
                        try:
                            from tkinter import simpledialog
                            
                            # Öncelikle TMDB ID'sini sor
                            tmdb_id_input = simpledialog.askstring(
                                "Klasör TMDB ID",
                                f"'{folder_name}' klasörü için TMDB ID'sini girin:\n\n"
                                f"Bu ID'yi themoviedb.org sitesinden bulabilirsiniz.\n"
                                f"Boş bırakırsanız medya türü sorulacaktır.",
                                parent=self.root
                            )
                            
                            if tmdb_id_input and tmdb_id_input.strip().isdigit():
                                folder_tmdb_id = int(tmdb_id_input.strip())
                                
                                # TMDB ID verildiğinde medya türünü sor
                                media_type_choice = simpledialog.askstring(
                                    "Klasör Medya Türü",
                                    f"TMDB ID: {folder_tmdb_id}\n"
                                    f"Bu ID hangi türde medyaya ait?\n"
                                    "1. Film\n"
                                    "2. TV Dizisi\n"
                                    "Seçiminizi yapın (1/2):",
                                    parent=self.root
                                )
                                
                                if media_type_choice == "1":
                                    folder_media_type = "movie"
                                elif media_type_choice == "2":
                                    folder_media_type = "tv"
                                else:
                                    # Varsayılan medya türü film
                                    folder_media_type = "movie"
                                    
                                self.log_progress(f"'{folder_name}' için TMDB ID: {folder_tmdb_id} ({folder_media_type})")
                            else:
                                # TMDB ID verilmediyse medya türünü sor
                                media_type_choice = simpledialog.askstring(
                                    "Klasör İçerik Türü",
                                    f"'{folder_name}' klasöründeki içeriklerin türünü seçin:\n"
                                    "1. Film\n"
                                    "2. TV Dizisi\n"
                                    "Seçiminizi yapın (1/2) veya boş bırakın (otomatik):",
                                    parent=self.root
                                )
                                
                                if media_type_choice == "1":
                                    folder_media_type = "movie"
                                    self.log_progress(f"'{folder_name}' klasörü için Film türü seçildi")
                                elif media_type_choice == "2":
                                    folder_media_type = "tv"
                                    self.log_progress(f"'{folder_name}' klasörü için TV Dizisi türü seçildi")
                        except Exception as e:
                            self.log_progress(f"Kullanıcı girişi alınırken hata: {str(e)}")
                            # Hatada varsayılan değerleri kullan

                # Her video için TMDB bilgisi çıkar
                folder_videos = []
                for i, video_file in enumerate(video_files):
                    try:
                        filename = os.path.basename(video_file)
                        self.log_progress(f"İşleniyor ({i+1}/{len(video_files)}): {filename}")
                        
                        # Eğer kullanıcı her dosya için giriş yapmak istiyorsa
                        video_tmdb_id = folder_tmdb_id
                        video_media_type = folder_media_type
                        video_ask_user = False
                        
                        # Klasör düzeyinde TMDB ID verilmediyse ve kullanıcı istiyorsa her dosya için sor
                        if ask_user_input and not folder_tmdb_id and not folder_media_type:
                            video_ask_user = True
                            
                        video_info = epg_generator.extract_video_info_with_tmdb(
                            video_file, 
                            progress_callback=self.log_progress,
                            ask_user=video_ask_user,  # Sadece kullanıcı istiyorsa ve klasör için ID belirlenmemişse sor
                            parent_window=self.root,
                            default_media_type=video_media_type,  # Klasör için seçilen medya tipini kullan
                            default_tmdb_id=video_tmdb_id  # Klasör için seçilen TMDB ID'sini kullan
                        )
                        folder_videos.append(video_info)
                    except Exception as e:
                        self.log_progress(f"Dosya işleme hatası: {str(e)}")
                
                # Klasör videolarını ekle
                if folder_videos:  # Boş değilse ekle
                    video_files_by_folder[folder_name] = folder_videos
                else:
                    self.log_progress(f"⚠️ '{folder_name}' klasöründe işlenebilecek video bulunamadı")
            except Exception as e:
                self.log_progress(f"❌ Klasör işleme hatası: {str(e)}")
        
        if not video_files_by_folder:
            self.log_progress("❌ EPG oluşturulamıyor: İşlenebilir video bulunamadı!")
            self.root.after(0, lambda: self.generate_epg_btn.config(state="normal"))
            return
        
        # EPG oluştur
        self.log_progress(f"EPG dosyası oluşturuluyor: {epg_filename} ({days} gün)")
        epg_content = epg_generator.generate_epg_from_videos(video_files_by_folder, days)
        
        # EPG dosyasını kaydet
        epg_output_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), epg_filename)
        epg_generator.save_epg(epg_output_path, epg_content)
        
        # Tamamlandı
        self.log_progress(f"✅ EPG dosyası başarıyla oluşturuldu: {epg_output_path}")
        
        # UI'yi güncelle - thread güvenli olması için
        self.root.after(0, lambda: self._on_epg_generation_complete(epg_output_path))
        
    except Exception as e:
        error_msg = f"EPG oluşturma hatası: {str(e)}"
        self.log_progress(f"❌ {error_msg}")
        logger.error(error_msg, exc_info=True)
        
        # UI'yi güncelle - thread güvenli olması için
        self.root.after(0, lambda: self.generate_epg_btn.config(state="normal"))
    
    def _on_epg_generation_complete(self, epg_file_path):
        """EPG oluşturma tamamlandığında çağrılır"""
        self.generate_epg_btn.config(state="normal")
        
        # Tamamlandı mesajı göster
        messagebox.showinfo("Başarılı", 
                          f"EPG dosyası başarıyla oluşturuldu:\n{epg_file_path}\n\n"
                          "Bu EPG dosyasını kanallarınıza bağlamak için:\n"
                          "1. Kanal sekmesine geçin\n"
                          "2. Kanalı seçin\n"
                          "3. EPG URL kısmına bu dosyanın yolunu yazın")
    
    def view_epg_file(self):
        """Oluşturulan EPG dosyasını görüntüle"""
        epg_filename = self.epg_filename_var.get().strip()
        if not epg_filename.endswith(".xml"):
            epg_filename += ".xml"
            
        epg_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), epg_filename)
        
        if os.path.exists(epg_path):
            # Dosyayı sistem varsayılan uygulaması ile aç
            try:
                os.startfile(epg_path)
            except AttributeError:
                # Linux/Mac için
                try:
                    import subprocess
                    subprocess.Popen(['xdg-open', epg_path])
                except:
                    messagebox.showerror("Hata", "EPG dosyası açılamıyor!")
        else:
            messagebox.showwarning("Uyarı", f"EPG dosyası bulunamadı: {epg_path}")

    def update_server_buttons(self):
        """Sunucu durumuna göre butonları güncelle"""
        if self.server_running:
            self.start_server_btn.config(state="disabled")
            self.stop_server_btn.config(state="normal")
            self.start_channels_btn.config(state="normal")
        else:
            self.start_server_btn.config(state="normal")
            self.stop_server_btn.config(state="disabled")
            self.start_channels_btn.config(state="disabled")
    
    def toggle_autostart(self):
        """Otomatik başlatma özelliğini aç/kapa"""
        self.autostart = self.autostart_var.get()
        # Ayarları kaydet
        config = {
            "channels": self.channels,
            "http_port": self.http_port,
            "autostart": self.autostart
        }
        try:
            with open(self.config_file, "w") as f:
                json.dump(config, f, indent=4)
            self.status_var.set("Otomatik başlatma ayarı kaydedildi")
        except Exception as e:
            messagebox.showerror("Hata", f"Yapılandırma kaydedilemedi: {str(e)}")
    
    def auto_start_server(self):
        """Otomatik başlatma için HTTP sunucusunu ve kanalları başlat"""
        if not self.server_running:
            self.start_http_server()
            
            # Sunucu başlatıldıktan sonra 2 saniye bekle, ardından kanalları başlat
            self.root.after(2000, self.start_all_channels)
    
    def show_help(self):
        """Yardım penceresini göster"""
        help_window = tk.Toplevel(self.root)
        help_window.title("IPTV Yönetim Sistemi - Kullanım Kılavuzu")
        help_window.geometry("600x500")
        help_window.minsize(600, 500)
        
        notebook = ttk.Notebook(help_window)
        
        # İşlem adımları sekmesi
        steps_frame = ttk.Frame(notebook)
        notebook.add(steps_frame, text="İşlem Adımları")
        
        steps_text = tk.Text(steps_frame, wrap=tk.WORD, padx=10, pady=10)
        steps_text.pack(fill="both", expand=True)
        
        steps_content = """
IPTV Kanal Yönetim Sistemi - Kullanım Adımları

1. KANAL EKLEME
   a) 'Kanal Adı' ve 'Port' alanlarını doldurun
   b) 'Klasör Ekle' butonuna tıklayın ve video klasörlerini seçin
   c) Birden fazla klasör ekleyebilirsiniz
   d) 'Kanal Ekle' butonuna tıklayın

2. SUNUCU BAŞLATMA
   a) 'HTTP Sunucusu Başlat' butonuna tıklayın
   b) Durum çubuğunda sunucunun başladığına dair mesaj görünecektir

3. KANALLARI BAŞLATMA
   a) Tüm kanalları başlatmak için 'Tüm Kanalları Başlat' butonuna tıklayın
   b) Seçili bir kanalı başlatmak için önce listeden bir kanal seçin, 
      sonra 'Seçili Kanalı Başlat' butonuna tıklayın

4. IPTV LİSTESİ OLUŞTURMA
   a) 'M3U Listesi Oluştur' butonuna tıklayarak IPTV oynatıcılar için liste oluşturun
   b) Oluşturulan liste 'TumKanallar.m3u' dosyasına kaydedilecektir

5. OTOMATİK BAŞLATMA
   a) Her program başlatıldığında sunucunun ve kanalların otomatik başlamasını istiyorsanız
      'Ayarlar' menüsünden 'Otomatik Başlat' seçeneğini işaretleyin
"""
        steps_text.insert(tk.END, steps_content)
        steps_text.config(state=tk.DISABLED)
        
        # SSS sekmesi
        faq_frame = ttk.Frame(notebook)
        notebook.add(faq_frame, text="Sık Sorulan Sorular")
        
        faq_text = tk.Text(faq_frame, wrap=tk.WORD, padx=10, pady=10)
        faq_text.pack(fill="both", expand=True)
        
        faq_content = """
Sık Sorulan Sorular

S: Sunucu başlatıldı mesajı alıyorum ancak kanallar çalışmıyor?
C: FFmpeg'in doğru şekilde yüklü olduğundan ve PATH'e eklendiğinden emin olun.
   Ayrıca klasörlerde video dosyalarının bulunduğunu kontrol edin.

S: IPTV oynatıcımda kanalları göremiyorum?
C: M3U listesinin doğru oluşturulduğundan emin olun. Bilgisayarınızın güvenlik
   duvarı 8080 portuna erişime izin vermeyebilir. Güvenlik duvarı ayarlarını kontrol edin.

S: Bir kanal ekledim ama videolar oynatılmıyor?
C: Klasörünüzde desteklenen formatta (.mp4, .mkv) video dosyaları olduğundan emin olun.
   Video dosyalarınızın izinleri ve yollarında özel karakter olmadığını kontrol edin.

S: Program kapanınca yayınlar duruyor mu?
C: Evet, program kapatıldığında tüm yayınlar otomatik olarak durdurulur.
   Sistemin sürekli çalışması için programın açık kalması gerekir.
"""
        faq_text.insert(tk.END, faq_content)
        faq_text.config(state=tk.DISABLED)
        
        notebook.pack(fill="both", expand=True)
    
    def show_about(self):
        """Hakkında penceresini göster"""
        messagebox.showinfo(
            "Hakkında",
            "IPTV Kanal Yönetim Sistemi\n\n"
            "Bu uygulama, yerel video klasörlerinden IPTV kanalları oluşturmanızı sağlar.\n"
            "FFmpeg kullanarak videolarınızı HLS formatında yayınlar.\n\n"
            "Kodlama: GitHub Copilot yardımıyla\n"
            "Versiyon: 1.0"
        )
    
    def load_config(self):
        if os.path.exists(self.config_file):
            try:
                with open(self.config_file, "r") as f:
                    config = json.load(f)
                    self.channels = config.get("channels", [])
                    self.http_port = config.get("http_port", 8080)
                    self.autostart = config.get("autostart", False)
            except Exception as e:
                messagebox.showerror("Hata", f"Yapılandırma dosyası yüklenemedi: {str(e)}")
        else:
            # Varsayılan yapılandırma
            self.channels = [
                {"name": "Diziler", "paths": ["D:\\Diziler", "D:\\Eski Diziler"], "port": 8081, "status": "Durduruldu"},
                {"name": "Filmler", "paths": ["D:\\Filmler"], "port": 8082, "status": "Durduruldu"}
            ]
            self.save_config()

    def save_config(self):
        config = {
            "channels": self.channels,
            "http_port": self.http_port,
            "autostart": self.autostart
        }
        try:
            with open(self.config_file, "w") as f:
                json.dump(config, f, indent=4)
        except Exception as e:
            messagebox.showerror("Hata", f"Yapılandırma kaydedilemedi: {str(e)}")
    
    def refresh_channel_list(self):
        # Önce mevcut listeyi temizle
        for item in self.channel_tree.get_children():
            self.channel_tree.delete(item)
        
        # Kanalları listeye ekle
        for channel in self.channels:
            paths = channel.get("paths", [])
            folder_count = len(paths)
            
            self.channel_tree.insert("", "end", values=(
                channel["name"],
                folder_count,
                channel["port"],
                channel.get("status", "Durduruldu")
            ))
    
    def refresh_folder_list(self):
        # Önce mevcut listeyi temizle
        for item in self.folder_tree.get_children():
            self.folder_tree.delete(item)
        
        # Klasörleri listeye ekle
        for folder in self.temp_folders:
            self.folder_tree.insert("", "end", values=(folder,))

    def auto_assign_port(self):
        """Otomatik boş port numarası ata"""
        # Kullanılan portları topla
        used_ports = set()
        for channel in self.channels:
            used_ports.add(channel["port"])
        
        # 8081'den başlayarak boş port bul
        new_port = 8081
        while new_port in used_ports:
            new_port += 1
        
        # Port alanını güncelle
        self.port_entry.delete(0, tk.END)
        self.port_entry.insert(0, str(new_port))
        self.status_var.set(f"Otomatik port atandı: {new_port}")
        logger.info(f"Otomatik port atandı: {new_port}")

    def add_channel(self):
        logger.info("Kanal ekleme işlemi başlatıldı")
        name = self.name_entry.get().strip()
        port = self.port_entry.get().strip()
        
        # Validasyon kontrolleri
        if not name:
            logger.warning("Kanal adı girilmedi")
            messagebox.showerror("Hata", "Kanal adı girmelisiniz!")
            self.name_entry.focus_set()
            return
        
        if not port:
            # Otomatik port ata
            logger.info("Port numarası girilmemiş, otomatik atanıyor")
            self.auto_assign_port()
            port = self.port_entry.get().strip()
        
        if not self.temp_folders:
            logger.warning("Klasör eklemeden kanal ekleme denemesi")
            messagebox.showerror("Hata", "Önce 'Klasör Ekle' butonunu kullanarak en az bir klasör eklemelisiniz!")
            return
        
        try:
            port = int(port)
        except ValueError:
            logger.warning(f"Geçersiz port numarası: {port}")
            messagebox.showerror("Hata", "Port numarası geçerli bir sayı olmalıdır!")
            self.port_entry.focus_set()  # Dikkat odağını buraya getir
            return
        
        # Aynı isimde kanal var mı kontrol et
        for channel in self.channels:
            if channel["name"] == name:
                logger.warning(f"Aynı isimde kanal zaten var: {name}")
                messagebox.showerror("Hata", f"'{name}' adında bir kanal zaten var!")
                self.name_entry.focus_set()  # Dikkat odağını buraya getir
                return
              # Aynı port numarası var mı kontrol et
            if channel["port"] == port:
                logger.warning(f"Port zaten kullanımda: {port}")
                messagebox.showerror("Hata", f"Port {port} zaten kullanımda!")
                self.port_entry.focus_set()  # Dikkat odağını buraya getir
                return
        
        try:
            # Yeni kanalı ekle
            logger.info(f"Yeni kanal ekleniyor: {name}, Port: {port}, Klasör sayısı: {len(self.temp_folders)}")
              # Logo bilgisini al
            logo = self.logo_entry.get().strip()
            
            # EPG bilgilerini al
            epg_url = self.epg_url_entry.get().strip()
            epg_id = self.epg_id_entry.get().strip()
            
            # Önce kanal objesi oluştur
            new_channel = {
                "name": name,
                "paths": self.temp_folders.copy(),
                "port": port,
                "logo": logo if logo else "",  # Logo bilgisini ekle
                "epg_url": epg_url if epg_url else "",  # EPG URL bilgisini ekle
                "epg_id": epg_id if epg_id else "",  # EPG ID bilgisini ekle
                "status": "Durduruldu"
            }
            
            # Listeye ekle
            self.channels.append(new_channel)
            
            # Debug amaçlı kanal bilgisini logla
            logger.debug(f"Oluşturulan kanal: {json.dumps(new_channel)}")
            
            # Yapılandırmayı kaydet 
            self.save_config()
            
            # Listeyi güncelle - bu adımda kanal görünmezse debug edelim
            self.refresh_channel_list()
            
            # Kanal sayısını kontrol et ve log'a yaz
            logger.info(f"Kanal ekleme sonrası toplam kanal sayısı: {len(self.channels)}")
            
            # Kullanıcıya bildir
            self.status_var.set(f"'{name}' kanalı eklendi - Artık kanalı başlatabilirsiniz")
            
            # Kanal ekle butonunu normal hale getir
            self.add_btn.config(text="Kanal Ekle", width=15, style="TButton")
            
            # Klasör ekleme butonlarını yeniden aktif yap
            self.add_folder_btn.config(state="normal") 
            self.remove_folder_btn.config(state="normal")
            
            # Alanları temizle
            self.clear_form()
            
            # Başarılı bir şekilde eklendiğini bildir
            messagebox.showinfo(
                "Başarılı", 
                f"'{name}' kanalı başarıyla eklendi.\n\n"
                f"Kanalı başlatmak için:\n"
                f"1. Kanallar listesinden '{name}' kanalını seçin\n"
                f"2. 'Seçili Kanalı Başlat' butonuna tıklayın"
            )
            
        except Exception as e:
            logger.error(f"Kanal eklenirken hata: {str(e)}", exc_info=True)
            messagebox.showerror("Hata", f"Kanal eklenirken hata oluştu: {str(e)}")

    def update_channel(self):
        if self.editing_index < 0:
            return
            
        name = self.name_entry.get().strip()
        port = self.port_entry.get().strip()
        
        # Validasyon kontrolleri
        if not name or not port:
            messagebox.showerror("Hata", "Kanal adı ve port numarası gerekli!")
            return
        
        if not self.temp_folders:
            messagebox.showerror("Hata", "En az bir klasör eklemelisiniz!")
            return
        
        try:
            port = int(port)
        except ValueError:
            messagebox.showerror("Hata", "Port numarası geçerli bir sayı olmalıdır!")
            return
        
        # Diğer kanallarda aynı isim var mı kontrol et
        for i, channel in enumerate(self.channels):
            if i != self.editing_index and channel["name"] == name:
                messagebox.showerror("Hata", f"'{name}' adında başka bir kanal zaten var!")
                return
          # Kanalı güncelle
        old_name = self.channels[self.editing_index]["name"]
        status = self.channels[self.editing_index].get("status", "Durduruldu")
          # Logo bilgisini al
        logo = self.logo_entry.get().strip()
        
        # EPG bilgilerini al
        epg_url = self.epg_url_entry.get().strip()
        epg_id = self.epg_id_entry.get().strip()
        
        self.channels[self.editing_index] = {
            "name": name,
            "paths": self.temp_folders.copy(),
            "port": port,
            "logo": logo if logo else "",  # Logo bilgisini ekle
            "epg_url": epg_url if epg_url else "",  # EPG URL bilgisini ekle
            "epg_id": epg_id if epg_id else "",  # EPG ID bilgisini ekle
            "status": status
        }
        
        # Yapılandırmayı kaydet ve listeyi güncelle
        self.save_config()
        self.refresh_channel_list()
        
        # Alanları temizle
        self.clear_form()
        
        self.status_var.set(f"'{old_name}' kanalı güncellendi")
    def delete_channel(self):
        if self.editing_index < 0:
            return
            
        channel_name = self.channels[self.editing_index]["name"]
        
        # Kanal çalışıyorsa durdur
        if self.channels[self.editing_index].get("status") == "Çalışıyor" and channel_name in self.ffmpeg_processes:
            self.stop_channel(channel_name)
        
        # Kanalı sil
        self.channels.pop(self.editing_index)
        
        # Yapılandırmayı kaydet ve listeyi güncelle
        self.save_config()
        self.refresh_channel_list()
        
        # Alanları temizle
        self.clear_form()
        
        self.status_var.set(f"'{channel_name}' kanalı silindi")
    
    def on_channel_select(self, event):
        selected = self.channel_tree.selection()
        if not selected:
            return
        
        index = self.channel_tree.index(selected[0])
        if index < 0 or index >= len(self.channels):
            return
        
        channel = self.channels[index]
        self.editing_index = index
          # Form alanlarını doldur
        self.name_entry.delete(0, tk.END)
        self.name_entry.insert(0, channel["name"])
        
        self.port_entry.delete(0, tk.END)
        self.port_entry.insert(0, str(channel["port"]))
          # Logo alanını doldur
        self.logo_entry.delete(0, tk.END)
        logo = channel.get("logo", "")
        if logo:
            self.logo_entry.insert(0, logo)
        
        # EPG alanlarını doldur
        self.epg_url_entry.delete(0, tk.END)
        epg_url = channel.get("epg_url", "")
        if epg_url:
            self.epg_url_entry.insert(0, epg_url)
            
        self.epg_id_entry.delete(0, tk.END)
        epg_id = channel.get("epg_id", "")
        if epg_id:
            self.epg_id_entry.insert(0, epg_id)
        
        # Klasör listesini doldur
        self.temp_folders = channel.get("paths", []).copy()
        self.refresh_folder_list()
        
        # Butonları etkinleştir        self.update_btn.config(state="normal")
        self.delete_btn.config(state="normal")
        self.start_channel_btn.config(state="normal")
        self.stop_channel_btn.config(state="normal")
        status = channel.get("status", "Durduruldu")
        if status == "Çalışıyor":
            self.start_channel_btn.config(state="disabled")
            self.stop_channel_btn.config(state="normal")
        else:
            self.start_channel_btn.config(state="normal")
            self.stop_channel_btn.config(state="disabled")
    
    def clear_form(self):
        """Form alanlarını temizle"""
        self.name_entry.delete(0, tk.END)
        self.port_entry.delete(0, tk.END)
        self.logo_entry.delete(0, tk.END)  # Logo alanını da temizle
        self.epg_url_entry.delete(0, tk.END)  # EPG URL alanını da temizle
        self.epg_id_entry.delete(0, tk.END)  # EPG ID alanını da temizle
        self.temp_folders = []
        self.refresh_folder_list()
        self.editing_index = -1
        
        # Butonları sıfırla
        self.update_btn.config(state="disabled")
        self.delete_btn.config(state="disabled")
        self.start_channel_btn.config(state="disabled")
        self.stop_channel_btn.config(state="disabled")
        
        # Klasör ekleme butonlarını tekrar etkinleştir
        self.add_folder_btn.config(state="normal") 
        self.remove_folder_btn.config(state="normal")
        self.assign_folders_btn.config(state="normal")  # Bu satırı geri ekliyoruz
        
        # Kanal ekle butonu normal görünümüne dönsün
        if hasattr(self, 'add_btn'):
            self.add_btn.config(text="KANAL EKLE", width=30, style="Action.TButton")

    def toggle_http_server(self):
        if not self.server_running:
            # HTTP sunucusunu başlat
            self.start_http_server()
            self.server_btn.config(text="HTTP Sunucusunu Durdur")
        else:
            # HTTP sunucusunu durdur
            self.stop_http_server()
            self.server_btn.config(text="HTTP Sunucusu Başlat")
    
    def start_http_server(self):
        if self.server_running:
            return
        
        try:
            # HLS çıktı klasörünü oluştur
            if not os.path.exists(self.output_dir):
                os.makedirs(self.output_dir)
            
            # HTTP sunucusunu ayrı bir thread'də başlat
            self.server_thread = threading.Thread(target=self._run_http_server, daemon=True)
            self.server_thread.start()
            
            self.server_running = True
            self.status_var.set(f"HTTP sunucusu port {self.http_port} üzerinde başlatıldı")
            
            # Butonları güncelle
            self.update_server_buttons()
        except Exception as e:
            logger.error(f"HTTP sunucusu başlatılırken hata: {str(e)}")
            messagebox.showerror("Hata", f"HTTP sunucusu başlatılamadı: {str(e)}")
    
    def _run_http_server(self):
        try:
            os.chdir(self.output_dir)
            handler = http.server.SimpleHTTPRequestHandler
            self.httpd = socketserver.TCPServer(("", self.http_port), handler)
            self.httpd.serve_forever()
        except Exception as e:
            logger.error(f"HTTP sunucu hatası: {str(e)}")
            print(f"HTTP sunucu hatası: {str(e)}")
    
    def stop_http_server(self):
        if not self.server_running:
            return
        
        try:
            self.httpd.shutdown()
            self.server_running = False
            self.status_var.set("HTTP sunucusu durduruldu")
            
            # Butonları güncelle
            self.update_server_buttons()
        except Exception as e:
            logger.error(f"HTTP sunucusu durdurulurken hata: {str(e)}")
            messagebox.showerror("Hata", f"HTTP sunucusu durdurulamadı: {str(e)}")
    
    def start_all_channels(self):
        if not self.server_running:
            messagebox.showwarning("Uyarı", "Önce HTTP sunucusunu başlatın!")
            return
        
        for channel in self.channels:
            channel_name = channel["name"]
            if channel.get("status") != "Çalışıyor":
                self.start_channel(channel_name)
    
    def stop_all_channels(self):
        for channel in self.channels:
            channel_name = channel["name"]
            if channel.get("status") == "Çalışıyor":
                self.stop_channel(channel_name)
    
    def start_selected_channel(self):
        if self.editing_index < 0:
            return
            
        channel_name = self.channels[self.editing_index]["name"]
        self.start_channel(channel_name)
    
    def stop_selected_channel(self):
        if self.editing_index < 0:
            return
            
        channel_name = self.channels[self.editing_index]["name"]
        self.stop_channel(channel_name)
    
    def start_channel(self, channel_name):
        # Kanalı bul
        channel = None
        for ch in self.channels:
            if ch["name"] == channel_name:
                channel = ch
                break
        
        if not channel:
            return
        
        # Kanal zaten çalışıyor mu kontrol et
        if channel.get("status") == "Çalışıyor" and channel_name in self.ffmpeg_processes:
            return
        
        # Kanal dizini ve oynatma listesi oluştur
        channel_dir = os.path.join(self.output_dir, channel_name)
        if not os.path.exists(channel_dir):
            os.makedirs(channel_dir)
        
        # Oynatma listesi oluştur
        playlist_file = os.path.join(channel_dir, "playlist.txt")
        video_files = []
        
        # Tüm klasörleri tara (alt klasörler dahil)
        paths = channel.get("paths", [])
        for path in paths:
            if os.path.exists(path):
                # MP4 dosyalarını bul
                for root, _, _ in os.walk(path):
                    mp4_files = glob.glob(os.path.join(root, "*.mp4"))
                    mkv_files = glob.glob(os.path.join(root, "*.mkv"))
                    video_files.extend(mp4_files + mkv_files)
        
        if not video_files:
            messagebox.showwarning("Uyarı", f"'{channel_name}' kanalı için video dosyası bulunamadı!")
            return
        
        with open(playlist_file, "w", encoding="utf-8") as f:
            for video in video_files:
                f.write(f"file '{video}'\n")
        
        # FFmpeg komutunu hazırla ve başlat
        port = channel["port"]
        output_file = os.path.join(channel_dir, f"{channel_name}.m3u8")
        
        try:
            ffmpeg_cmd = [
                "ffmpeg", "-re", "-f", "concat", "-safe", "0", "-i", playlist_file,
                "-c", "copy", "-f", "hls", "-hls_time", "4", "-hls_playlist_type", "event",
                "-hls_flags", "append_list+omit_endlist", 
                "-hls_segment_filename", os.path.join(channel_dir, "segment_%03d.ts"),
                output_file
            ]
            
            logger.info(f"FFmpeg komutu: {' '.join(ffmpeg_cmd)}")
            process = subprocess.Popen(ffmpeg_cmd)
            self.ffmpeg_processes[channel_name] = process
            
            # Kanal durumunu güncelle
            for ch in self.channels:
                if ch["name"] == channel_name:
                    ch["status"] = "Çalışıyor"
                    break
            
            self.save_config()
            self.refresh_channel_list()
            
            if channel_name == self.channels[self.editing_index]["name"]:
                self.start_channel_btn.config(state="disabled")
                self.stop_channel_btn.config(state="normal")
                
            self.status_var.set(f"'{channel_name}' kanalı başlatıldı")
        except Exception as e:
            logger.error(f"Kanal başlatılırken hata: {str(e)}")
            messagebox.showerror("Hata", f"Kanal başlatılamadı: {str(e)}")
    
    def stop_channel(self, channel_name):
        if channel_name in self.ffmpeg_processes:
            try:
                # FFmpeg işlemini durdur
                process = self.ffmpeg_processes[channel_name]
               

                process.terminate()
                process.wait(timeout=5)
                
                # İşlem listesinden kaldır
                del self.ffmpeg_processes[channel_name]
                
                # Kanal durumunu güncelle
                for ch in self.channels:
                    if ch["name"] == channel_name:
                        ch["status"] = "Durduruldu"
                        break
                
                self.save_config()
                self.refresh_channel_list()
                
                if self.editing_index >= 0 and channel_name == self.channels[self.editing_index]["name"]:
                    self.start_channel_btn.config(state="normal")
                    self.stop_channel_btn.config(state="disabled")
                
                self.status_var.set(f"'{channel_name}' kanalı durduruldu")
            except Exception as e:
                logger.error(f"Kanal durdurulurken hata: {str(e)}")
                messagebox.showerror("Hata", f"Kanal durdurulamadı: {str(e)}")
    
    def create_m3u_playlist(self):
        if not self.channels:
            messagebox.showwarning("Uyarı", "Kanal listesi boş!")
            return
        
        try:            # Yerel IP adresini al
            hostname = socket.gethostname()
            ip_address = socket.gethostbyname(hostname)
            
            # M3U dosyasını oluştur
            playlist_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "TumKanallar.m3u")
            
            with open(playlist_path, "w", encoding="utf-8") as f:
                f.write("#EXTM3U\n")
                for channel in self.channels:
                    # Logo ve EPG bilgilerini al
                    logo = channel.get("logo", "")
                    epg_url = channel.get("epg_url", "")
                    epg_id = channel.get("epg_id", "")
                    
                    # EXTINF satırını oluştur - logo ve EPG varsa ekle
                    extinf_line = f'#EXTINF:-1 tvg-id="{epg_id if epg_id else channel["name"]}" tvg-name="{channel["name"]}" group-title="Yerel"'
                    if logo:
                        extinf_line += f' tvg-logo="{logo}"'
                    if epg_url:
                        extinf_line += f' tvg-epg="{epg_url}"'
                    extinf_line += f',{channel["name"]}\n'
                    
                    f.write(extinf_line)
                    f.write(f'http://{ip_address}:{self.http_port}/{channel["name"]}/{channel["name"]}.m3u8\n')
            
            messagebox.showinfo("Bilgi", f"M3U oynatma listesi oluşturuldu: {playlist_path}")
        except Exception as e:
            logger.error(f"M3U dosyası oluşturulurken hata: {str(e)}")
            messagebox.showerror("Hata", f"M3U dosyası oluşturulamadı: {str(e)}")
    
    # Yeni metot: Hata günlüğü gösterme
    def show_error_log(self):
        """Hata günlüğü dosyasını göster"""
        if os.path.exists(log_file):
            log_window = tk.Toplevel(self.root)
            log_window.title("Hata Günlüğü")
            log_window.geometry("800x600")
            
            # Araç çubuğu
            toolbar = ttk.Frame(log_window)
            toolbar.pack(side=tk.TOP, fill=tk.X)
            
            refresh_btn = ttk.Button(toolbar, text="Yenile", command=lambda: self.load_log_file(log_text))
            refresh_btn.pack(side=tk.LEFT, padx=5, pady=5)
            
            copy_btn = ttk.Button(toolbar, text="Kopyala", 
                                 command=lambda: self.copy_log_to_clipboard(log_text))
            copy_btn.pack(side=tk.LEFT, padx=5, pady=5)
            
            open_btn = ttk.Button(toolbar, text="Log Klasörünü Aç", 
                                 command=lambda: os.startfile(log_dir))
            open_btn.pack(side=tk.LEFT, padx=5, pady=5)
            
            # Log içeriği için metin alanı
            frame = ttk.Frame(log_window)
            frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
            
            scrollbar = ttk.Scrollbar(frame)
            scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
            
            log_text = tk.Text(frame, wrap=tk.WORD, yscrollcommand=scrollbar.set)
            log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
            scrollbar.config(command=log_text.yview)
            
            # Log dosyasını yükle
            self.load_log_file(log_text)
            
            # Durumu göster
            status_bar = ttk.Label(log_window, text=f"Log dosyası: {log_file}", relief=tk.SUNKEN, anchor=tk.W)
            status_bar.pack(side=tk.BOTTOM, fill=tk.X)
        else:
            messagebox.showinfo("Bilgi", "Henüz bir hata günlüğü oluşturulmamış.")
    
    def load_log_file(self, text_widget):
        """Log dosyasını oku ve metin alanına yükle"""
        text_widget.delete(1.0, tk.END)
        try:
            with open(log_file, 'r', encoding='utf-8') as f:
                content = f.read()
                text_widget.insert(tk.END, content)
                # Sona kaydır
                text_widget.see(tk.END)
        except Exception as e:
            logger.error(f"Log dosyası yüklenemedi: {str(e)}")
            text_widget.insert(tk.END, f"Hata: Log dosyası yüklenemedi! {str(e)}")
    
    def copy_log_to_clipboard(self, text_widget):
        """Log içeriğini panoya kopyala"""
        self.root.clipboard_clear()
        self.root.clipboard_append(text_widget.get(1.0, tk.END))
        messagebox.showinfo("Bilgi", "Log içeriği panoya kopyalandı!")
        logger.info("Log içeriği panoya kopyalandı")
    
    def add_folder(self):
        """Klasör seçme ve ekleme işlevi"""
        try:
            folder_path = filedialog.askdirectory(parent=self.root, title="Klasör Seç")
            if folder_path:
                logger.info(f"Klasör seçildi: {folder_path}")
                
                # Aynı klasör zaten var mı kontrol et
                if folder_path in self.temp_folders:
                    messagebox.showwarning("Uyarı", "Bu klasör zaten eklenmiş!")
                    return
                
                # Klasörün var olduğunu kontrol et
                if not os.path.exists(folder_path):
                    logger.error(f"Klasör bulunamadı: {folder_path}")
                    messagebox.showerror("Hata", f"Klasör bulunamadı: {folder_path}")
                    return
                
                # Klasörü ekle
                self.temp_folders.append(folder_path)
                self.refresh_folder_list()
                
                # Kullanıcıya bilgi ver - mesajı güncelliyoruz
                self.status_var.set(f"Klasör eklendi: {folder_path}. Kanal adı ve port bilgilerini girerek 'KANAL EKLE' butonuna tıklayın.")
                
                # Port alanı boşsa otomatik port ata
                if not self.port_entry.get():
                    self.auto_assign_port()
                  # Log kayıtı
                logger.info(f"Klasör başarıyla eklendi: {folder_path}")
                logger.info(f"Toplam {len(self.temp_folders)} klasör eklenmiş durumda")
                
        except Exception as e:
            logger.error(f"Klasör eklenirken hata: {str(e)}")
            messagebox.showerror("Hata", f"Klasör eklenirken bir hata oluştu:\n{str(e)}")
    
    def remove_folder(self):
        """Seçili klasörü listeden kaldırır"""
        selected = self.folder_tree.selection()
        if not selected:
            messagebox.showwarning("Uyarı", "Lütfen silinecek bir klasör seçin!")
            return
        
        index = self.folder_tree.index(selected[0])
        if index < 0 or index >= len(self.temp_folders):
            return
        
        removed_folder = self.temp_folders.pop(index)
        self.refresh_folder_list()
        self.status_var.set(f"Klasör kaldırıldı: {removed_folder}")
        logger.info(f"Klasör kaldırıldı: {removed_folder}")
    
    def select_logo_file(self):
        """Logo dosyası seçme"""
        try:
            # Desteklenen resim formatları
            filetypes = [
                ("Resim Dosyaları", "*.png *.jpg *.jpeg *.gif *.bmp *.svg"),
                ("PNG", "*.png"),
                ("JPEG", "*.jpg *.jpeg"),
                ("GIF", "*.gif"),
                ("SVG", "*.svg"),
                ("Tüm Dosyalar", "*.*")
            ]
            
            file_path = filedialog.askopenfilename(
                parent=self.root,
                title="Logo Dosyası Seçin",
                filetypes=filetypes
            )
            
            if file_path:
                # Dosyanın var olduğunu kontrol et
                if not os.path.exists(file_path):
                    messagebox.showerror("Hata", f"Dosya bulunamadı: {file_path}")
                    return
                  # Logo entry'sine dosya yolunu yerleştir
                self.logo_entry.delete(0, tk.END)
                self.logo_entry.insert(0, file_path)
                self.status_var.set(f"Logo dosyası seçildi: {os.path.basename(file_path)}")
                logger.info(f"Logo dosyası seçildi: {file_path}")
                
        except Exception as e:
            logger.error(f"Logo dosyası seçerken hata: {str(e)}")
            messagebox.showerror("Hata", f"Logo dosyası seçerken hata oluştu: {str(e)}")
    
    def clear_logo(self):
        """Logo alanını temizle"""
        self.logo_entry.delete(0, tk.END)
        self.status_var.set("Logo temizlendi")
        logger.info("Logo alanı temizlendi")

    def select_epg_file(self):
        """EPG dosyası seç"""
        try:
            # Desteklenen EPG dosya formatları
            filetypes = [
                ('XML Dosyaları', '*.xml'),
                ('XMLTV Dosyaları', '*.xmltv'),
                ('Tüm Dosyalar', '*.*')
            ]
            
            file_path = filedialog.askopenfilename(
                parent=self.root,
                title="EPG Dosyası Seçin",
                filetypes=filetypes
            )
            
            if file_path:
                # Dosyanın var olduğunu kontrol et
                if not os.path.exists(file_path):
                    messagebox.showerror("Hata", f"Dosya bulunamadı: {file_path}")
                    return
                
                # EPG URL entry'sine dosya yolunu yerleştir
                self.epg_url_entry.delete(0, tk.END)
                self.epg_url_entry.insert(0, file_path)
                self.status_var.set(f"EPG dosyası seçildi: {os.path.basename(file_path)}")
                logger.info(f"EPG dosyası seçildi: {file_path}")
                
        except Exception as e:
            logger.error(f"EPG dosyası seçerken hata: {str(e)}")
            messagebox.showerror("Hata", f"EPG dosyası seçerken hata oluştu: {str(e)}")
    
    def clear_epg(self):
        """EPG alanlarını temizle"""
        self.epg_url_entry.delete(0, tk.END)
        self.epg_id_entry.delete(0, tk.END)
        self.status_var.set("EPG bilgileri temizlendi")
        logger.info("EPG alanları temizlendi")

    def assign_folders_to_channel(self):
        """Seçilen klasörleri kanala atama için hazırlar"""
        if not self.temp_folders:
            messagebox.showerror("Hata", "Önce klasör eklemelisiniz!")
            return
            
        # Klasör sayısını kontrol et
        folder_count = len(self.temp_folders)
        
        # Kanal ekle butonu için yönlendirme mesajı
        messagebox.showinfo(
            "Klasörler Hazır",
            f"{folder_count} klasör kanalınız için hazırlandı.\n\n"
            "ŞİMDİ:\n"
            "1. Kanal adı girin\n"
            "2. Port numarası girin (veya otomatik atansın)\n"
            "3. 'KANAL EKLE' butonuna tıklayın"
        )
        
        # Port alanı boşsa otomatik port ata
        if not self.port_entry.get():
            self.auto_assign_port()
            
        # Kullanıcının dikkatini kanal adı alanına çekmek için
        self.name_entry.focus_set()
        
        # Durumu güncelle
        self.status_var.set(f"{folder_count} klasör hazır → Kanal adı ve port girin → 'KANAL EKLE' butonuna basın")
        logger.info(f"{folder_count} klasör kanalınız için hazırlandı")
# Ana uygulama çalıştırma fonksiyonu
def main():
    """Ana uygulama fonksiyonu"""
    # Gereksinimleri kontrol et
    if not check_requirements():
        sys.exit(1)
        
    # Ana pencereyi oluştur
    root = tk.Tk()
    app = IPTVManagerApp(root)
    
    # Pencereyi ortala
    window_width = 900
    window_height = 650
    screen_width = root.winfo_screenwidth()
    screen_height = root.winfo_screenheight()
    center_x = int((screen_width - window_width) / 2)
    center_y = int((screen_height - window_height) / 2)
    root.geometry(f'{window_width}x{window_height}+{center_x}+{center_y}')
    
    # Çıkışta kanalları durdur
    def on_closing():
        if messagebox.askokcancel("Çıkış", "Programdan çıkmak istediğinize emin misiniz?\nTüm kanallar durdurulacaktır."):
            app.stop_all_channels()
            root.destroy()
    
    root.protocol("WM_DELETE_WINDOW", on_closing)
    
    try:
        # Ana döngüyü başlat
        root.mainloop()
    except Exception as e:
        logger.error(f"Uygulama hatası: {str(e)}", exc_info=True)
        messagebox.showerror("Hata", f"Beklenmeyen bir hata oluştu!\n{str(e)}")

if __name__ == "__main__":
    main()

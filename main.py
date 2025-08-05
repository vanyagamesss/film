# main.py - Основной скрипт приложения "Киноман"
# Использует Eel для GUI, OpenCV для превью, ffprobe для метаданных видео

import eel
import os
import sys
import json
import subprocess  # Для запуска ffprobe и внешних плееров
import platform  # Для определения ОС (Windows, macOS, Linux)
import shutil  # Для копирования/удаления файлов
from PIL import Image  # Pillow для работы с изображениями, используется при обработке превью
import cv2  # OpenCV для захвата кадров видео

try:
    from tqdm import tqdm  # Для отображения прогресса в консоли при сканировании
except ImportError:
    tqdm = None  # Если tqdm не установлен, используем простой цикл
import uuid  # Для генерации уникальных ID фильмов
import re  # Для парсинга года из названия фильма
import time  # Для отметки времени добавления фильма
import urllib.parse  # Для кодирования URL-путей
import webbrowser  # Для fallback-открытия в системном браузере

# --- Конфигурация папок ---
# Получаем директорию, где запущен скрипт
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# Пути к папкам для фильмов, превью и базы данных
# ВНИМАНИЕ: Для старых версий Eel, папки MOVIES_DIR и THUMBNAILS_DIR
# должны находиться ВНУТРИ папки 'web', чтобы Eel мог их обслуживать.
# Например, web/movies и web/thumbnails
web_dir = os.path.join(SCRIPT_DIR, 'web')
MOVIES_DIR = os.path.join(web_dir, 'movies')  # ИЗМЕНЕНО: теперь movies находится внутри web
THUMBNAILS_DIR = os.path.join(web_dir, 'thumbnails')  # ИЗМЕНЕНО: теперь thumbnails находится внутри web
DB_FILE = os.path.join(SCRIPT_DIR, 'movies_db.json')  # DB_FILE может оставаться рядом с main.py

# Создаем необходимые папки, если их нет
os.makedirs(MOVIES_DIR, exist_ok=True)
os.makedirs(THUMBNAILS_DIR, exist_ok=True)

# Поддерживаемые расширения видеофайлов
SUPPORTED_FORMATS = ('.mp4', '.avi', '.mkv', '.mov', '.wmv', '.flv', '.webm', '.m4v', '.3gp')


def get_video_metadata_with_ffprobe(file_path):
    """
    Использует ffprobe для получения длительности и других метаданных видео.
    Возвращает словарь с 'duration' (секунды), 'width', 'height'.
    """
    try:
        # Команда ffprobe для получения информации о длительности и разрешении в формате JSON
        cmd = [
            'ffprobe',
            '-v', 'error',  # Выводить только ошибки
            '-select_streams', 'v:0',  # Выбрать только первый видеопоток
            '-show_entries', 'stream=duration,width,height',  # Показать длительность, ширину, высоту
            '-of', 'json',  # Вывод в формате JSON
            file_path
        ]

        # Запускаем ffprobe как подпроцесс
        result = subprocess.run(cmd, capture_output=True, text=True, check=True,
                                creationflags=subprocess.CREATE_NO_WINDOW if platform.system() == "Windows" else 0)

        # Парсим JSON-вывод
        data = json.loads(result.stdout)

        duration = 0
        width = 0
        height = 0

        # Извлекаем данные из JSON-ответа
        if 'streams' in data and len(data['streams']) > 0:
            stream = data['streams'][0]
            if 'duration' in stream:
                try:
                    duration = float(stream['duration'])
                except ValueError:
                    duration = 0  # Если длительность не является числом
            width = stream.get('width', 0)
            height = stream.get('height', 0)

        return {'duration': int(duration), 'width': width, 'height': height}
    except FileNotFoundError:
        print("Ошибка: ffprobe не найден. Убедитесь, что FFmpeg установлен и добавлен в PATH.")
        return {'duration': 0, 'width': 0, 'height': 0}
    except subprocess.CalledProcessError as e:
        print(f"Ошибка при вызове ffprobe для {file_path}: {e.stderr.strip()}")
        return {'duration': 0, 'width': 0, 'height': 0}
    except json.JSONDecodeError as e:
        print(f"Ошибка парсинга JSON от ffprobe для {file_path}: {e}")
        return {'duration': 0, 'width': 0, 'height': 0}
    except Exception as e:
        print(f"Неизвестная ошибка при получении метаданных для {file_path}: {e}")
        return {'duration': 0, 'width': 0, 'height': 0}


def generate_thumbnail_with_opencv(file_path, thumbnail_path):
    """
    Генерирует миниатюру видео, используя OpenCV.
    Попытается взять кадр на 10% от длительности или первый кадр.
    """
    cap = cv2.VideoCapture(file_path)
    if not cap.isOpened():
        print(f"Не удалось открыть видеофайл для миниатюры: {file_path}")
        return False

    # Получаем общую длительность в миллисекундах и FPS
    frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    fps = cap.get(cv2.CAP_PROP_FPS)
    total_duration_ms = (frame_count / fps) * 1000 if fps > 0 else 0

    # Устанавливаем позицию для захвата кадра
    if total_duration_ms > 0:
        target_ms = total_duration_ms * 0.1
        cap.set(cv2.CAP_PROP_POS_MSEC, target_ms)
    else:
        cap.set(cv2.CAP_PROP_POS_FRAMES, 0)  # Устанавливаем на первый кадр

    ret, frame = cap.read()  # Читаем кадр
    cap.release()  # Освобождаем ресурсы

    if ret:
        # Конвертируем BGR в RGB
        img = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
        # Ресайз до 300x300, сохраняя пропорции
        img.thumbnail((300, 300), Image.Resampling.LANCZOS)
        img.save(thumbnail_path, quality=85)
        return True
    else:
        print(f"Не удалось захватить кадр для миниатюры из: {file_path}")
        return False


class MovieManager:
    """Класс для управления коллекцией фильмов: загрузка, сохранение, сканирование, CRUD операции."""

    def __init__(self, db_file):
        self.db_file = db_file
        self.movies = self._load_movies()
        self._ensure_ids_are_strings()  # Убеждаемся, что ID в БД хранятся как строки

    def _load_movies(self):
        """Загружает базу данных фильмов из JSON-файла."""
        if os.path.exists(self.db_file):
            try:
                with open(self.db_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except json.JSONDecodeError:
                print(f"Предупреждение: База данных повреждена. Создаю новую.")
                return []
            except Exception as e:
                print(f"Ошибка загрузки БД: {e}")
                return []
        print("База данных не найдена. Создаю новую.")
        return []

    def _save_movies(self):
        """Сохраняет текущее состояние базы данных фильмов в JSON-файл."""
        try:
            with open(self.db_file, 'w', encoding='utf-8') as f:
                json.dump(self.movies, f, indent=4, ensure_ascii=False)
        except Exception as e:
            print(f"Ошибка сохранения БД: {e}")

    def _ensure_ids_are_strings(self):
        """Преобразует любые числовые ID в строковые."""
        changed = False
        for movie in self.movies:
            if 'id' in movie and not isinstance(movie['id'], str):
                movie['id'] = str(movie['id'])
                changed = True
        if changed:
            self._save_movies()

    def scan_movies(self):
        """
        Сканирует папку с фильмами, добавляет новые, обновляет существующие
        и удаляет отсутствующие файлы из базы данных.
        """
        found_movies = []
        existing_movies_by_path = {os.path.normcase(os.path.normpath(m['path'])): m for m in self.movies}

        print("\n--- Запуск сканирования фильмов ---")
        all_files_to_scan = [os.path.join(root, filename) for root, _, files in os.walk(MOVIES_DIR) for filename in
                             files]

        iterator = tqdm(all_files_to_scan, desc="Сканирование файлов") if tqdm else all_files_to_scan
        for file_path in iterator:
            normalized_path = os.path.normcase(os.path.normpath(file_path))
            if not normalized_path.lower().endswith(SUPPORTED_FORMATS):
                continue

            existing_movie = existing_movies_by_path.pop(normalized_path, None)
            if existing_movie:
                found_movies.append(existing_movie)
                continue

            print(f"Обработка нового фильма: {os.path.basename(file_path)}")
            metadata = get_video_metadata_with_ffprobe(file_path)
            duration_seconds = metadata.get('duration', 0)
            width = metadata.get('width', 0)
            height = metadata.get('height', 0)

            title = os.path.splitext(os.path.basename(file_path))[0].replace('.', ' ').strip().title()
            year = "Неизвестен"
            match = re.search(r'(\d{4})', title)
            if match:
                year = match.group(1)

            genre = "Неизвестен"
            rating = 0.0
            description = "Нет описания."

            thumbnail_filename = f"{uuid.uuid4().hex}.jpg"
            thumbnail_path = os.path.join(THUMBNAILS_DIR, thumbnail_filename)
            if not generate_thumbnail_with_opencv(file_path, thumbnail_path):
                thumbnail_filename = None

            file_size = os.path.getsize(file_path)

            new_movie = {
                'id': str(uuid.uuid4()),
                'title': title,
                'path': os.path.normpath(file_path),  # Сохраняем оригинальный путь
                'genre': genre,
                'year': year,
                'rating': rating,
                'duration': duration_seconds,
                'resolution': f"{width}x{height}" if width and height else 'Unknown',
                'size': file_size,
                'thumbnail': thumbnail_filename,
                'description': description,
                'date_added': int(time.time())
            }
            found_movies.append(new_movie)

        # Удаление отсутствующих фильмов
        removed_count = 0
        for movie_path_to_remove, movie_data_to_remove in existing_movies_by_path.items():
            print(f"Удаление отсутствующего фильма: {movie_data_to_remove['title']}")
            if movie_data_to_remove.get('thumbnail'):
                thumbnail_path = os.path.join(THUMBNAILS_DIR, movie_data_to_remove['thumbnail'])
                try:
                    if os.path.exists(thumbnail_path):
                        os.remove(thumbnail_path)
                        print(f"Удалено превью: {thumbnail_path}")
                except Exception as e:
                    print(f"Ошибка удаления превью: {e}")
            removed_count += 1

        self.movies = found_movies
        self._save_movies()
        print(f"Сканирование завершено. Обнаружено {len(found_movies)} фильмов. Удалено {removed_count}.")
        return self.movies

    def get_movies(self):
        return self.movies

    def get_movie_details(self, movie_id):
        return next((m for m in self.movies if m['id'] == movie_id), None)

    def update_movie_info(self, movie_id, title, genre, year, rating, description):
        for movie in self.movies:
            if movie['id'] == movie_id:
                movie['title'] = title
                movie['genre'] = genre
                movie['year'] = int(year)
                movie['rating'] = float(rating)
                movie['description'] = description
                self._save_movies()
                return {'success': True}
        return {'success': False, 'error': 'Фильм не найден.'}

    def delete_movie(self, movie_id):
        """Удаляет фильм из базы данных, файл с диска и превью."""
        for i, movie in enumerate(self.movies):
            if movie['id'] == movie_id:
                movie_to_delete = self.movies.pop(i)
                # Удаление файла фильма
                try:
                    if os.path.exists(movie_to_delete['path']):
                        os.remove(movie_to_delete['path'])
                        print(f"Удален файл: {movie_to_delete['path']}")
                except Exception as e:
                    print(f"Ошибка удаления файла: {e}")

                # Удаление превью
                if movie_to_delete.get('thumbnail'):
                    thumbnail_path = os.path.join(THUMBNAILS_DIR, movie_to_delete['thumbnail'])
                    try:
                        if os.path.exists(thumbnail_path):
                            os.remove(thumbnail_path)
                            print(f"Удалено превью: {thumbnail_path}")
                    except Exception as e:
                        print(f"Ошибка удаления превью: {e}")

                self._save_movies()
                return {'success': True}
        return {'success': False, 'error': 'Фильм не найден.'}

    def get_movies_stats(self):
        total_movies = len(self.movies)
        total_size = sum(m.get('size', 0) for m in self.movies)
        total_duration = sum(m.get('duration', 0) for m in self.movies)
        valid_ratings = [m.get('rating', 0) for m in self.movies if isinstance(m.get('rating'), (int, float))]
        avg_rating = round(sum(valid_ratings) / len(valid_ratings), 1) if valid_ratings else 0.0
        return {
            'total_movies': total_movies,
            'total_size': total_size,
            'total_duration': total_duration,
            'avg_rating': avg_rating
        }

    def add_movie_from_path(self, file_path):
        normalized_path = os.path.normpath(file_path)
        if not os.path.exists(normalized_path):
            return {'success': False, 'error': 'Файл не существует.'}
        if not normalized_path.lower().endswith(SUPPORTED_FORMATS):
            return {'success': False, 'error': 'Неподдерживаемый формат.'}

        # Проверка на дубликат
        if any(os.path.normpath(m['path']) == normalized_path for m in self.movies):
            return {'success': False, 'error': 'Фильм уже существует.'}

        original_filename = os.path.basename(normalized_path)
        destination_path = os.path.join(MOVIES_DIR, original_filename)
        counter = 1
        base_name, extension = os.path.splitext(original_filename)
        while os.path.exists(destination_path):
            destination_path = os.path.join(MOVIES_DIR, f"{base_name}_{counter}{extension}")
            counter += 1

        try:
            shutil.copy2(normalized_path, destination_path)
        except Exception as e:
            return {'success': False, 'error': f'Ошибка копирования: {e}'}

        self.scan_movies()
        new_movie = next((m for m in self.movies if os.path.normpath(m['path']) == os.path.normpath(destination_path)),
                         None)
        if new_movie:
            return {'success': True, 'movie': new_movie}
        return {'success': False, 'error': 'Не удалось добавить в БД.'}

    def update_movie_thumbnail(self, movie_id):
        """Обновляет превью фильма, открывая диалог выбора изображения."""
        try:
            import tkinter as tk
            from tkinter import filedialog

            root = tk.Tk()
            root.withdraw()
            root.attributes('-topmost', True)

            thumbnail_path = filedialog.askopenfilename(
                title="Выберите изображение для превью",
                filetypes=[("Изображения", "*.jpg *.jpeg *.png *.gif"), ("Все файлы", "*.*")]
            )
            root.destroy()

            if not thumbnail_path:
                return {'success': False, 'error': 'Выбор отменен.'}

            movie = self.get_movie_details(movie_id)
            if not movie:
                return {'success': False, 'error': 'Фильм не найден.'}

            # Удаление старого превью, если есть
            if movie.get('thumbnail'):
                old_thumbnail_path = os.path.join(THUMBNAILS_DIR, movie['thumbnail'])
                try:
                    if os.path.exists(old_thumbnail_path):
                        os.remove(old_thumbnail_path)
                        print(f"Удалено старое превью: {old_thumbnail_path}")
                except Exception as e:
                    print(f"Ошибка удаления старого превью: {e}")

            # Генерация уникального имени и копирование
            new_thumbnail_filename = f"{uuid.uuid4().hex}{os.path.splitext(thumbnail_path)[1]}"
            new_thumbnail_path = os.path.join(THUMBNAILS_DIR, new_thumbnail_filename)

            img = Image.open(thumbnail_path)
            img.thumbnail((300, 300), Image.Resampling.LANCZOS)
            img.save(new_thumbnail_path, quality=85)

            movie['thumbnail'] = new_thumbnail_filename
            self._save_movies()
            print(f"Превью обновлено для фильма {movie['title']}: {new_thumbnail_filename}")
            return {'success': True, 'thumbnail': new_thumbnail_filename}
        except ImportError:
            return {'success': False, 'error': 'Tkinter не установлен.'}
        except Exception as e:
            return {'success': False, 'error': f'Ошибка обновления превью: {e}'}


# Создаем экземпляр менеджера фильмов
movie_manager = MovieManager(DB_FILE)


# --- Eel Exposing Functions ---

@eel.expose
def get_movies():
    try:
        movies_data = movie_manager.scan_movies()
        return {'success': True, 'movies': movies_data}
    except Exception as e:
        return {'success': False, 'error': str(e)}


@eel.expose
def search_movies(query):
    if not movie_manager.movies:
        movie_manager.scan_movies()
    query = query.lower()
    results = [m for m in movie_manager.movies if
               query in m['title'].lower() or query in m['genre'].lower() or query in m['description'].lower() or str(
                   m['year']) == query]
    return results


@eel.expose
def get_movies_stats():
    return movie_manager.get_movies_stats()


@eel.expose
def prepare_movie_for_playback(movie_path):
    if not os.path.exists(movie_path):
        return {'success': False, 'error': 'Файл не найден.'}
    try:
        if not os.path.exists(web_dir):
            return {'success': False, 'error': 'Веб-директория не найдена.'}
        path_relative_to_web_dir = os.path.relpath(movie_path, web_dir)
        local_url = '/' + urllib.parse.quote(path_relative_to_web_dir.replace(os.sep, '/'))
        return {'success': True, 'local_url': local_url}
    except Exception as e:
        return {'success': False, 'error': str(e)}


@eel.expose
def get_movie_details(movie_id):
    return movie_manager.get_movie_details(movie_id)


@eel.expose
def update_movie_info(movie_id, title, genre, year, rating, description):
    return movie_manager.update_movie_info(movie_id, title, genre, year, rating, description)


@eel.expose
def delete_movie(movie_id):
    return movie_manager.delete_movie(movie_id)


@eel.expose
def browse_for_movie():
    try:
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk()
        root.withdraw()
        root.attributes('-topmost', True)
        file_path = filedialog.askopenfilename(
            title="Выберите видеофайл",
            filetypes=[("Видеофайлы", "*.mp4 *.avi *.mkv *.mov *.wmv *.flv *.webm *.m4v *.3gp"), ("Все файлы", "*.*")]
        )
        root.destroy()
        if file_path:
            return movie_manager.add_movie_from_path(file_path)
        return {'success': False, 'error': 'Выбор отменен.'}
    except Exception as e:
        return {'success': False, 'error': str(e)}


@eel.expose
def update_movie_thumbnail(movie_id):
    return movie_manager.update_movie_thumbnail(movie_id)


# Инициализация Eel
if not os.path.exists(web_dir):
    print(f"Ошибка: Веб-директория не найдена: {web_dir}")
    sys.exit(1)

eel.init(web_dir)

print("==================================================")
print("🎬 КИНОМАН - Ваша личная коллекция фильмов")
print("==================================================")
# ... (остальной вывод без изменений)

# --- Нормальный запуск с fallback ---
print("\n--- Попытка нормального запуска приложения ---")
BROWSER_LAUNCH_MODES = ['chrome-app', 'edge', 'chrome', 'default']
LAUNCHED_SUCCESSFULLY = False

for mode in BROWSER_LAUNCH_MODES:
    try:
        print(f"Попытка запуска в режиме '{mode}'...")
        eel.start('index.html', size=(1400, 900), mode=mode)
        LAUNCHED_SUCCESSFULLY = True
        print(f"✅ Успешный запуск в режиме '{mode}'.")
        break
    except Exception as e:
        print(f"❌ Ошибка в режиме '{mode}': {e}")

if not LAUNCHED_SUCCESSFULLY:
    print("\n--- Все стандартные режимы провалились. Переход в серверный режим (fallback) ---")
    print("   Запускаю локальный сервер Eel и открываю в системном браузере...")
    try:
        # Запуск Eel в серверном режиме (без автоматического браузера, на случайном порту)
        # port=0 - Eel выберет свободный порт
        eel.start('index.html', mode=False, host='localhost', port=0, block=False)

        # Получаем порт, на котором запустился сервер (Eel хранит его в eel._port)
        port = eel._websockets_port  # Это внутренний атрибут, но он работает в старых версиях Eel

        # Формируем URL
        url = f'http://localhost:{port}/index.html'
        print(f"✅ Сервер запущен на {url}")
        print("   Открываю в системном браузере... Если не открылось, перейдите по ссылке вручную.")

        # Открываем в дефолтном браузере
        webbrowser.open(url)

        # Блокируем скрипт, чтобы сервер продолжал работать (бесконечный цикл)
        while True:
            time.sleep(1)  # Держим сервер живым
    except Exception as e:
        print(f"❌ Ошибка в fallback-режиме: {e}")
        print("   Убедитесь, что установлен браузер и порт свободен.")
        print("   Попробуйте открыть http://localhost:8000/index.html вручную после запуска скрипта.")
        sys.exit(1)

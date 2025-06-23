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
from tqdm import tqdm  # Для отображения прогресса в консоли при сканировании
import uuid  # Для генерации уникальных ID фильмов
import re  # Для парсинга года из названия фильма
import time  # Для отметки времени добавления фильма
import urllib.parse  # Для кодирования URL-путей

# --- Конфигурация папок ---
# Получаем директорию, где запущен скрипт
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# Пути к папкам для фильмов, превью и базы данных
# ВНИМАНИЕ: Для старых версий Eel, папки MOVIES_DIR и THUMBNAILS_DIR
# должны находиться ВНУТРИ папки 'web', чтобы Eel мог их обслуживать.
# Например, web/movies и web/thumbnails
MOVIES_DIR = os.path.join(SCRIPT_DIR, 'web', 'movies')  # ИЗМЕНЕНО: теперь movies находится внутри web
THUMBNAILS_DIR = os.path.join(SCRIPT_DIR, 'web', 'thumbnails')  # ИЗМЕНЕНО: теперь thumbnails находится внутри web
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
        # creationflags=subprocess.CREATE_NO_WINDOW предотвращает появление черного окна консоли в Windows
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
        # Если ffprobe не найден в PATH
        print("Ошибка: ffprobe не найден. Убедитесь, что FFmpeg установлен и добавлен в PATH.")
        return {'duration': 0, 'width': 0, 'height': 0}
    except subprocess.CalledProcessError as e:
        # Если ffprobe вернул ошибку (например, файл поврежден)
        print(f"Ошибка при вызове ffprobe для {file_path}: {e.stderr.strip()}")
        return {'duration': 0, 'width': 0, 'height': 0}
    except json.JSONDecodeError as e:
        # Если вывод ffprobe не является корректным JSON
        print(f"Ошибка парсинга JSON от ffprobe для {file_path}: {e}")
        return {'duration': 0, 'width': 0, 'height': 0}
    except Exception as e:
        # Любая другая неожиданная ошибка
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
    # Берем кадр на 10% от длительности видео. Если видео очень короткое или FPS=0, берем первый кадр.
    if total_duration_ms > 0:
        target_ms = total_duration_ms * 0.1
        cap.set(cv2.CAP_PROP_POS_MSEC, target_ms)
    else:
        cap.set(cv2.CAP_PROP_POS_FRAMES, 0)  # Устанавливаем на первый кадр

    ret, frame = cap.read()  # Читаем кадр
    cap.release()  # Важно освободить ресурсы после использования

    if ret:
        # OpenCV читает кадры в формате BGR, PIL.Image ожидает RGB. Конвертируем.
        img = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))

        # Изменяем размер миниатюры, сохраняя пропорции, до максимального размера 300x300
        img.thumbnail((300, 300), Image.Resampling.LANCZOS)  # LANCZOS для лучшего качества уменьшения
        img.save(thumbnail_path, quality=85)  # Сохраняем с качеством 85%
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
                print(
                    f"Предупреждение: База данных фильмов ({os.path.basename(self.db_file)}) повреждена или нечитаема. Будет создана новая.")
                return []
            except Exception as e:
                print(
                    f"Неизвестная ошибка при загрузке БД фильмов ({os.path.basename(self.db_file)}): {e}. Будет создана новая.")
                return []
        print(f"База данных фильмов ({os.path.basename(self.db_file)}) не найдена. Будет создана новая.")
        return []

    def _save_movies(self):
        """Сохраняет текущее состояние базы данных фильмов в JSON-файл."""
        try:
            with open(self.db_file, 'w', encoding='utf-8') as f:
                json.dump(self.movies, f, indent=4, ensure_ascii=False)
            print(f"База данных фильмов успешно сохранена в {os.path.basename(self.db_file)}")
        except Exception as e:
            print(f"Ошибка сохранения БД фильмов в {os.path.basename(self.db_file)}: {e}")

    def _ensure_ids_are_strings(self):
        """Преобразует любые числовые ID в строковые для консистентности."""
        changed = False
        for movie in self.movies:
            if 'id' in movie and not isinstance(movie['id'], str):
                movie['id'] = str(movie['id'])
                changed = True
        if changed:
            self._save_movies()  # Сохраняем, если были изменения

    def scan_movies(self):
        """
        Сканирует папку с фильмами, добавляет новые, обновляет существующие
        и удаляет отсутствующие файлы из базы данных.
        """
        found_movies = []  # Список фильмов, найденных на диске
        # Создаем словарь для быстрого поиска существующих фильмов по пути
        existing_movies_by_path = {os.path.normpath(m['path']): m for m in self.movies}

        print("\n--- Запуск сканирования фильмов ---")
        # Итерируем по всем файлам в папке MOVIES_DIR и ее подпапках
        all_files_to_scan = []
        for root, _, files in os.walk(MOVIES_DIR):
            for filename in files:
                all_files_to_scan.append(os.path.join(root, filename))

        for file_path in tqdm(all_files_to_scan, desc="Сканирование файлов"):
            normalized_path = os.path.normpath(file_path)

            # Проверяем, является ли файл поддерживаемым видеоформатом
            if not normalized_path.lower().endswith(SUPPORTED_FORMATS):
                continue  # Пропускаем неподдерживаемые файлы

            # Проверяем, есть ли фильм уже в базе данных
            existing_movie = existing_movies_by_path.pop(normalized_path,
                                                         None)  # Удаляем из временного словаря, чтобы отслеживать удаленные

            if existing_movie:
                # Если фильм уже есть, добавляем его в список найденных
                found_movies.append(existing_movie)
                continue  # Переходим к следующему файлу

            # Если фильм новый, обрабатываем его
            print(f"Обработка нового фильма: {os.path.basename(file_path)}")
            try:
                # Получаем метаданные видео с помощью ffprobe
                metadata = get_video_metadata_with_ffprobe(file_path)
                duration_seconds = metadata.get('duration', 0)
                width = metadata.get('width', 0)
                height = metadata.get('height', 0)

                # Генерируем название и пытаемся извлечь год
                title = os.path.splitext(os.path.basename(file_path))[0].replace('.', ' ').strip().title()
                year = "Неизвестен"
                match = re.search(r'(\d{4})', title)
                if match:
                    year = match.group(1)

                genre = "Неизвестен"
                rating = 0.0
                description = "Нет описания."

                # Генерируем уникальное имя для миниатюры
                thumbnail_filename = f"{uuid.uuid4().hex}.jpg"
                thumbnail_path = os.path.join(THUMBNAILS_DIR, thumbnail_filename)

                # Генерируем миниатюру с помощью OpenCV
                if not generate_thumbnail_with_opencv(file_path, thumbnail_path):
                    thumbnail_filename = None  # Если не удалось создать миниатюру

                # Получаем размер файла
                file_size = os.path.getsize(file_path)

                new_movie = {
                    'id': str(uuid.uuid4()),  # Генерируем уникальный строковый ID
                    'title': title,
                    'path': normalized_path,
                    'genre': genre,
                    'year': year,
                    'rating': rating,
                    'duration': duration_seconds,
                    'resolution': f"{width}x{height}" if width and height else 'Unknown',
                    'size': file_size,
                    'thumbnail': thumbnail_filename,
                    'description': description,
                    'date_added': int(time.time())  # Время добавления
                }
                found_movies.append(new_movie)
            except Exception as e:
                print(f"Ошибка при обработке фильма {os.path.basename(file_path)}: {e}")
                # Продолжаем сканирование, даже если один файл вызвал ошибку

        # Отслеживаем и удаляем фильмы, которых больше нет на диске
        removed_count = 0
        for movie_path_to_remove, movie_data_to_remove in existing_movies_by_path.items():
            print(
                f"Фильм не найден на диске, удаляем из базы: {movie_data_to_remove['title']} ({movie_path_to_remove})")
            if movie_data_to_remove.get('thumbnail') and os.path.exists(
                    os.path.join(THUMBNAILS_DIR, movie_data_to_remove['thumbnail'])):
                try:
                    os.remove(os.path.join(THUMBNAILS_DIR, movie_data_to_remove['thumbnail']))
                    print(f"Удалено превью: {movie_data_to_remove['thumbnail']}")
                    # Удалить thumbnail_path из movie_data_to_remove
                    movie_data_to_remove['thumbnail'] = None
                except Exception as e:
                    print(f"Не удалось удалить превью {movie_data_to_remove['thumbnail']}: {e}")
            removed_count += 1

        self.movies = found_movies  # Обновляем список фильмов в менеджере
        self._save_movies()
        print(
            f"Сканирование завершено. Обнаружено {len(found_movies)} фильмов. Удалено {removed_count} отсутствующих фильмов из БД.")
        return self.movies

    def get_movies(self):
        """Возвращает текущий список всех фильмов."""
        return self.movies

    def get_movie_details(self, movie_id):
        """Возвращает детальную информацию о фильме по его ID."""
        movie = next((m for m in self.movies if m['id'] == movie_id), None)
        return movie

    def update_movie_info(self, movie_id, title, genre, year, rating, description):
        """Обновляет метаданные фильма."""
        try:
            for movie in self.movies:
                if movie['id'] == movie_id:
                    movie['title'] = title
                    movie['genre'] = genre
                    movie['year'] = int(year)
                    movie['rating'] = float(rating)
                    movie['description'] = description
                    self._save_movies()
                    print(f"Информация о фильме '{title}' (ID: {movie_id}) успешно обновлена.")
                    return {'success': True}
            print(f"Ошибка обновления: Фильм с ID '{movie_id}' не найден.")
            return {'success': False, 'error': 'Фильм не найден для обновления.'}
        except ValueError as e:
            print(f"Ошибка при обновлении информации о фильме (неверный формат данных): {e}")
            return {'success': False, 'error': f'Ошибка формата данных: {e}'}
        except Exception as e:
            print(f"Неизвестная ошибка при обновлении информации о фильме: {e}")
            return {'success': False, 'error': str(e)}

    def delete_movie(self, movie_id):
        """Удаляет фильм из базы данных и соответствующий файл с диска."""
        movie_to_delete = None
        # Ищем фильм и удаляем его из списка
        for i, movie in enumerate(self.movies):
            if movie['id'] == movie_id:
                movie_to_delete = self.movies.pop(i)  # Удаляем элемент и получаем его
                break

        if movie_to_delete:
            print(f"Попытка удаления фильма: {movie_to_delete.get('title', movie_id)}")
            # Попытка удаления файла фильма с диска
            try:
                if os.path.exists(movie_to_delete['path']):
                    os.remove(movie_to_delete['path'])
                    print(f"Файл фильма успешно удален: {movie_to_delete['path']}")
                else:
                    print(f"Файл фильма не найден на диске для удаления: {movie_to_delete['path']}")
            except Exception as e:
                print(f"Не удалось удалить файл фильма {movie_to_delete['path']}: {e}")

            # Попытка удаления файла миниатюры
            if movie_to_delete.get('thumbnail'):
                thumbnail_path = os.path.join(THUMBNAILS_DIR, movie_to_delete['thumbnail'])
                if os.path.exists(thumbnail_path):
                    try:
                        os.remove(thumbnail_path)
                        print(f"Файл превью успешно удален: {thumbnail_path}")
                        # Удалить thumbnail_path из movie_to_delete
                        movie_to_delete['thumbnail'] = None
                    except Exception as e:
                        print(f"Не удалось удалить файл превью {thumbnail_to_delete['thumbnail']}: {e}")

            self._save_movies()  # Сохраняем обновленную базу данных
            print(f"Фильм '{movie_to_delete.get('title', movie_id)}' успешно удален из базы данных.")
            return {'success': True}
        return {'success': False, 'error': 'Фильм не найден для удаления.'}

    def get_movies_stats(self):
        """Возвращает агрегированную статистику по коллекции фильмов."""
        total_movies = len(self.movies)
        total_size = sum(m.get('size', 0) for m in self.movies)
        total_duration = sum(m.get('duration', 0) for m in self.movies)

        # Расчет среднего рейтинга, учитывая только фильмы с числовым рейтингом
        valid_ratings = [m.get('rating', 0) for m in self.movies if isinstance(m.get('rating'), (int, float))]
        avg_rating = round(sum(valid_ratings) / len(valid_ratings), 1) if valid_ratings else 0.0

        return {
            'total_movies': total_movies,
            'total_size': total_size,
            'total_duration': total_duration,
            'avg_rating': avg_rating
        }

    def add_movie_from_path(self, file_path):
        """
        Добавляет фильм в коллекцию по указанному пути к файлу.
        Фильм копируется в папку MOVIES_DIR.
        """
        original_normalized_path = os.path.normpath(file_path)

        if not os.path.exists(original_normalized_path):
            print(f"Ошибка добавления: Выбранный файл не существует: {original_normalized_path}")
            return {'success': False, 'error': 'Выбранный файл не существует.'}

        if not original_normalized_path.lower().endswith(SUPPORTED_FORMATS):
            print(f"Ошибка добавления: Неподдерживаемый формат файла: {original_normalized_path}")
            return {'success': False, 'error': 'Неподдерживаемый формат файла.'}

        # Создаем уникальное имя файла для копирования в MOVIES_DIR, чтобы избежать перезаписи
        original_filename = os.path.basename(original_normalized_path)
        destination_path = os.path.join(MOVIES_DIR, original_filename)

        counter = 1
        base_name = os.path.splitext(original_filename)[0]
        extension = os.path.splitext(original_filename)[1]
        while os.path.exists(destination_path):
            destination_path = os.path.join(MOVIES_DIR, f"{base_name}_{counter}{extension}")
            counter += 1

        print(f"Копирование файла: {original_normalized_path} -> {destination_path}")
        try:
            shutil.copy2(original_normalized_path, destination_path)  # Копируем файл (сохраняя метаданные)
            print(f"Файл успешно скопирован в: {destination_path}")
        except Exception as e:
            print(f"Ошибка копирования файла {original_normalized_path} в {destination_path}: {e}")
            return {'success': False, 'error': f'Ошибка копирования файла: {e}'}

        # После копирования, сканируем, чтобы добавить этот новый фильм в БД
        # scan_movies сам обнаружит этот файл и добавит его
        self.scan_movies()

        # Находим только что добавленный фильм в обновленном списке
        new_movie_data = next(
            (m for m in self.movies if os.path.normpath(m['path']) == os.path.normpath(destination_path)), None)

        if new_movie_data:
            print(f"Фильм '{new_movie_data['title']}' успешно добавлен и обработан.")
            return {'success': True, 'movie': new_movie_data}
        else:
            print(f"Ошибка: Не удалось найти только что скопированный фильм '{destination_path}' после сканирования.")
            return {'success': False, 'error': 'Не удалось добавить фильм в базу данных после копирования.'}


# Создаем экземпляр менеджера фильмов
movie_manager = MovieManager(DB_FILE)


# --- Eel Exposing Functions (Функции, доступные из JavaScript) ---

@eel.expose
def get_movies():
    """
    Получает список всех фильмов.
    Эта функция вызывает movie_manager.scan_movies() для обновления данных
    на основе файлов на диске.
    """
    try:
        movies_data = movie_manager.scan_movies()
        return {'success': True, 'movies': movies_data}
    except Exception as e:
        print(f"Python Ошибка в get_movies (во время сканирования/загрузки): {e}")
        return {'success': False, 'error': f'Ошибка при загрузке/сканировании фильмов: {e}'}


@eel.expose
def search_movies(query):
    """Ищет фильмы по названию или жанру."""
    # Убеждаемся, что фильмы загружены/отсканированы
    if not movie_manager.movies:
        movie_manager.scan_movies()  # Запускаем сканирование, если база пуста

    query = query.lower()
    results = [
        m for m in movie_manager.movies
        if query in m['title'].lower() or query in m['genre'].lower() or \
           query in m['description'].lower() or str(m['year']) == query
    ]
    return results


@eel.expose
def get_movies_stats():
    """Возвращает агрегированную статистику по коллекции фильмов."""
    return movie_manager.get_movies_stats()


@eel.expose
def prepare_movie_for_playback(movie_path):
    """
    Подготавливает видеофайл к воспроизведению в браузере, возвращая его локальный URL.
    Поскольку 'extra_paths' и 'add_static_route' недоступны в старой версии Eel,
    мы полагаемся на то, что папка 'movies' перемещена внутрь 'web_dir'.
    """
    if not os.path.exists(movie_path):
        print(f"Ошибка: Файл видео не найден по пути: {movie_path}")
        return {'success': False, 'error': 'Файл видео не найден.'}

    try:
        # Получаем относительный путь от 'web_dir' (D:\киноман\киноман\киноман\web)
        # Если MOVIES_DIR = D:\киноман\киноман\киноман\web\movies
        # И movie_path = D:\киноман\киноман\киноман\web\movies\my_film.mp4
        # Тогда os.path.relpath(movie_path, web_dir) даст 'movies\my_film.mp4'

        # SCRIPT_DIR = D:\киноман\киноман\киноман
        # web_dir = D:\киноман\киноман\киноман\web
        # MOVIES_DIR = D:\киноман\киноман\киноман\web\movies

        # path_relative_to_web_dir будет 'movies\my_film.mp4'
        path_relative_to_web_dir = os.path.relpath(movie_path, web_dir)

        # Формируем URL: заменяем обратные слеши на прямые и кодируем
        local_url = '/' + urllib.parse.quote(path_relative_to_web_dir.replace(os.sep, '/'))

        print(f"Подготовлен файл для воспроизведения: {movie_path} -> {local_url}")
        return {'success': True, 'local_url': local_url}
    except Exception as e:
        print(f"Ошибка при подготовке файла для воспроизведения {movie_path}: {e}")
        return {'success': False, 'error': f'Ошибка подготовки видео: {e}'}


@eel.expose
def get_movie_details(movie_id):
    """Получает детальную информацию о конкретном фильме."""
    return movie_manager.get_movie_details(movie_id)


@eel.expose
def update_movie_info(movie_id, title, genre, year, rating, description):
    """Обновляет информацию о фильме, введенную пользователем."""
    return movie_manager.update_movie_info(movie_id, title, genre, year, rating, description)


@eel.expose
def delete_movie(movie_id):
    """Удаляет фильм из базы данных и файл с диска."""
    return movie_manager.delete_movie(movie_id)


@eel.expose
def browse_for_movie():
    """
    Открывает системный диалог выбора файла для добавления нового фильма.
    Использует tkinter, который часто поставляется с Python.
    """
    try:
        import tkinter as tk
        from tkinter import filedialog

        # Создаем скрытое окно Tkinter
        root = tk.Tk()
        root.withdraw()  # Скрыть главное окно Tkinter
        root.attributes('-topmost', True)  # Сделать окно выбора файла поверх других окон

        file_path = filedialog.askopenfilename(
            title="Выберите видеофайл для добавления",
            filetypes=[("Видеофайлы", "*.mp4 *.avi *.mkv *.mov *.wmv *.flv *.webm *.m4v *.3gp"),
                       ("Все файлы", "*.*")]
        )
        root.destroy()  # Уничтожить окно Tkinter после использования

        if file_path:
            print(f"Выбран файл для добавления: {file_path}")
            return movie_manager.add_movie_from_path(file_path)
        else:
            print("Выбор файла отменен пользователем.")
            return {'success': False, 'error': 'Выбор файла отменен.'}
    except ImportError:
        print(
            "Ошибка: Модуль tkinter не найден. Пожалуйста, установите его (pip install tk) или используйте ручное добавление по пути.")
        return {'success': False, 'error': 'Tkinter не установлен. Используйте ручное добавление по пути.'}
    except Exception as e:
        print(f"Ошибка при открытии диалога выбора файла: {e}")
        return {'success': False, 'error': f'Ошибка при открынии диалога выбора файла: {e}'}


# Создаем экземпляр менеджера фильмов
movie_manager = MovieManager(DB_FILE)

# --- Инициализация Eel ---
# Указываем Eel путь к папке с веб-файлами
web_dir = os.path.join(SCRIPT_DIR, 'web')
if not os.path.exists(web_dir):
    print(f"Ошибка: Веб-директория не найдена по пути {web_dir}")
    print("Убедитесь, что ваши HTML, CSS и JS файлы находятся в папке 'web' рядом с main.py")
    sys.exit(1)

eel.init(web_dir)  # Инициализируем Eel

print("==================================================")
print("🎬 КИНОМАН - Ваша личная коллекция фильмов")
print("==================================================")
print(f"📁 Папка для фильмов: {MOVIES_DIR}")
print(f"🖼️  Папка для превью: {THUMBNAILS_DIR}")
print(f"💾 База данных: {DB_FILE}")
print("--------------------------------------------------")
print("📋 Поддерживаемые форматы:")
for fmt in SUPPORTED_FORMATS:
    print(f"    {fmt.upper().lstrip('.')}")
print("--------------------------------------------------")
print("🚀 Запуск веб-интерфейса...")
print("💡 Поместите ваши фильмы в папку 'movies' (теперь она должна быть внутри папки 'web')")
print("    и нажмите 'Сканировать фильмы' в интерфейсе")
print("==================================================")

# Улучшенная и более информативная логика запуска Eel для десктопного приложения
print("\n--- Попытка запуска Eel в режиме десктопного приложения ---")
print("   (Требует установленного Chrome/Edge и библиотеки 'browser_paths')")
BROWSER_LAUNCH_MODES = ['chrome-app', 'edge', 'chrome', 'default']
LAUNCHED_SUCCESSFULLY = False

for mode in BROWSER_LAUNCH_MODES:
    try:
        print(f"Trying to launch with mode='{mode}'...")
        # Удален аргумент extra_paths, так как он вызывает ошибку 'AttributeError'.
        # Теперь папки 'movies' и 'thumbnails' должны находиться внутри 'web_dir'.
        eel.start('index.html', size=(1400, 900), mode=mode)
        LAUNCHED_SUCCESSFULLY = True
        print(f"✅ Successfully launched with mode='{mode}'.")
        break  # Выход из цикла, если запуск успешен
    except Exception as e:
        print(f"❌ Failed to launch with mode='{mode}': {e}")
        if mode == 'chrome-app':
            print("   (Hint: 'chrome-app' requires Google Chrome to be installed.)")
        elif mode == 'edge':
            print("   (Hint: 'edge' requires Microsoft Edge to be installed, primarily for Windows.)")
        elif mode == 'chrome':
            print("   (Hint: 'chrome' requires Google Chrome to be installed.)")
        elif mode == 'default':
            print("   (Critical: 'default' mode failed. Check your default browser and system configuration.)")
        # Дополнительная подсказка, если не установлен browser_paths
        if "No browser found" in str(e) or "browser_paths" in str(e):
            print("   (Suggestion: Ensure 'browser_paths' is installed: pip install browser_paths)")

if not LAUNCHED_SUCCESSFULLY:
    print("\n--- Все попытки запуска Eel не удались ---")
    print("   Пожалуйста, проверьте следующие пункты:")
    print("   1. Установлены ли Google Chrome или Microsoft Edge?")
    print("   2. Установлена ли Python-библиотека 'browser_paths' (pip install browser_paths)?")
    print("   3. Есть ли доступ к Интернету (для загрузки eel.js в первый раз) и свободные порты?")
    print("   4. Нет ли проблем с вашим системным PATH, которые мешают Eel найти браузер?")
    sys.exit(1)  # Выход из приложения, если совсем не удалось запустить

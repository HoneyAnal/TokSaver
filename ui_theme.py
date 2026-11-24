"""Палитры (тёмная и светлая темы) и размеры интерфейса TokSaver."""
from i18n import tr
import customtkinter as ctk


# Мягкий современный стиль: скруглённые углы (RADIUS), спокойные поверхности
# разной светлоты (окно < панель < карточка), тонкая контрастная обводка, один
# синий акцент. Скроллбары — свои плоские (ui_widgets.FlatScrollbar).
RADIUS = 10
ctk.ThemeManager.theme["CTkFrame"]["corner_radius"] = RADIUS
ctk.ThemeManager.theme["CTkScrollbar"]["corner_radius"] = RADIUS

THEMES = {
    "dark": {
        "mode": "dark",
        "bg": "#15171c",       # фон окна
        "side": "#0f1115",     # сайдбар (темнее фона — зоны различимы)
        "card": "#1d2027",     # карточка / панель
        "card_h": "#282c36",   # наведение
        "field": "#13151a",    # поля ввода и вторичные кнопки
        "line": "#2e333d",     # обводка
        "shadow": "#0b0c0f",
        "accent": "#4f8cff",
        "accent_h": "#3b78ec",
        "on_accent": "#ffffff",
        "text": "#eceef3",
        "muted": "#8d94a3",
        "log_bg": "#101216",
        "log_text": "#b9bfcc",
        "thumb_bg": "#13151a",
        "ok": "#58d68d",
        "warn": "#f5b041",
        "danger": "#ff6b6b",
        "info": "#b9bfcc",
        "head": "#ffffff",
        "scroll": "#3a3f4b",
        "scroll_h": "#4d5361",
        "sel_line": "#3a3f4b",
        "log_dl": "#6fb1ff",     # что качается
        "log_sys": "#4dd0e1",    # компоненты и сеть
        "log_auth": "#c3a6ff",   # авторизация
        "log_sum": "#7ee8a8",    # итоги
        "log_dim": "#6b7385",    # подробности
    },
    "light": {
        "mode": "light",
        "bg": "#eef0f5",
        "side": "#f7f8fb",
        "card": "#ffffff",
        "card_h": "#eef1f6",
        "field": "#eceff4",
        "line": "#d5d9e2",
        "shadow": "#c8cdd8",
        "accent": "#2f6fed",
        "accent_h": "#2459d1",
        "on_accent": "#ffffff",
        "text": "#1b1e27",
        "muted": "#677084",
        "log_bg": "#ffffff",
        "log_text": "#3b4252",
        "thumb_bg": "#e9ecf2",
        "ok": "#1f9254",
        "warn": "#b26a00",
        "danger": "#d1364d",
        "info": "#3b4252",
        "head": "#0e1118",
        "scroll": "#c5cad6",
        "scroll_h": "#aeb4c2",
        "sel_line": "#cfd5e1",
        "log_dl": "#1d62d6",
        "log_sys": "#0e7c8c",
        "log_auth": "#7040c8",
        "log_sum": "#13794a",
        "log_dim": "#8b93a5",
    },
}

BROWSERS = [tr('none'), "chrome", "firefox", "edge", "brave", "opera", "vivaldi", "chromium"]

PREVIEW_W, PREVIEW_H, PREVIEW_R = 232, 148, 10   # обложка на карточке
AVATAR_SIZE = 120            # крупный аватар-кружок на карточке, пока нет обложки

# единый стиль кнопок: скруглённые углы, крупные названия вместо значков
BTN_H = 38
# толщина жёсткой тени под панелями/карточками (кромка справа и снизу)
SHADOW_W = 4

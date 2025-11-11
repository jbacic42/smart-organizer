from pathlib import Path
import shutil
import logging
from datetime import datetime
import json
import os
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from ttkthemes import ThemedTk
import threading
import sys

# =================================================================
#  Application Path Helper
# =================================================================

def get_writable_path():
    """
    Returns the correct path for writable files (logs, configs)
    whether running as a script or a "frozen" executable.
    """
    if getattr(sys, 'frozen', False):
        # We are running in a bundled (frozen) state (e.g., PyInstaller)
        # sys.argv[0] is the path to the executable
        return Path(sys.argv[0]).parent
    else:
        # We are running as a normal .py script
        return Path(__file__).parent

# =================================================================
#  Core Logic
# =================================================================

class FileOrganizer:
    """
    Handles the core business logic for scanning, organizing,
    and undoing file operations.
    """
    
    def __init__(self, source_directory, progress_callback=None):
        """
        Initializes the organizer for a specific source directory.

        Args:
            source_directory (str or Path): The target folder to organize.
            progress_callback (callable, optional): A function to call
                with progress updates (e.g., `callback(percentage)`).
        """
        self.source_directory = Path(source_directory) 
        if not self.source_directory.exists():
            raise FileNotFoundError(f"Error: Directory not found at {self.source_directory}")
        if not self.source_directory.is_dir():
            raise NotADirectoryError(f"Error: Path '{self.source_directory}' is not a directory.")
        
        # History file is stored *inside* the organized folder for portability.
        self.history_file = self.source_directory / '.organizer_history.json'
        self.progress_callback = progress_callback
        self._setup_logger()

    def _setup_logger(self):
        """Configures a shared logger for all instances."""
        self.logger = logging.getLogger('FileOrganizer')
        self.logger.setLevel(logging.INFO)
        
        # Avoid adding duplicate handlers if class is instantiated multiple times.
        if not self.logger.handlers:
            log_file = get_writable_path() / 'organizer.log'
            file_handler = logging.FileHandler(log_file)
            formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s', 
                                          datefmt='%Y-%m-%d %H:%M:%S')
            file_handler.setFormatter(formatter)
            self.logger.addHandler(file_handler)
            
    def _load_history_stack(self):
        """
        Loads the LIFO history stack from the JSON file.
        Each item in the stack is a list of (src, dest) tuples for one "run".
        
        Returns:
            list: The history stack, or an empty list if not found/corrupt.
        """
        if not self.history_file.exists():
            return []
        try:
            with open(self.history_file, 'r') as f:
                stack = json.load(f)
                return stack if isinstance(stack, list) else []
        except json.JSONDecodeError:
            return [] # Treat corrupt or empty file as empty history
        except Exception as e:
            self.logger.error(f"Error reading history file: {e}")
            return []
            
    def _report_progress(self, current, total):
        """Sends a progress update to the GUI thread via the callback."""
        if self.progress_callback and total > 0:
            percentage = (current / total) * 100
            self.progress_callback(percentage)

    def _move_file(self, item, destination_folder, history_list):
        """
        Atomically moves a single file, creates the destination folder,
        and records the move to the current batch history.

        Args:
            item (Path): The file to move.
            destination_folder (Path): The target folder.
            history_list (list): The list accumulating moves for this batch.

        Returns:
            bool: True on success, False on failure.
        """
        try:
            # `parents=True` handles both flat and nested structures.
            destination_folder.mkdir(parents=True, exist_ok=True)
            destination_path = destination_folder / item.name
            
            source_path_str = str(item)
            destination_path_str = str(destination_path)
            
            shutil.move(source_path_str, destination_path_str)
            
            history_list.append((source_path_str, destination_path_str))
            self.logger.info(f"Moved: {source_path_str} -> {destination_path_str}")
            return True
        except Exception as e:
            self.logger.error(f"Error moving {item.name}: {e}")
            return False

    def _set_file_hidden(self, file_path):
        """Sets the 'hidden' attribute for a file, primarily for Windows."""
        # On Windows, os.name is 'nt'.
        if os.name == 'nt':
            try:
                import ctypes
                # 2 = FILE_ATTRIBUTE_HIDDEN
                ret = ctypes.windll.kernel32.SetFileAttributesW(str(file_path), 2)
                if not ret:
                    self.logger.warning(f"Failed to set hidden attribute on {file_path}")
            except Exception as e:
                self.logger.warning(f"Could not set hidden attribute on {file_path}: {e}")

    def _build_keyword_map(self, keyword_rules, folder_rules):
        """
        Parses rule strings into a simple lookup map.
        (rest of docstring...)
        """
        keyword_map = {}
        keyword_groups = [g.strip() for g in keyword_rules.split(';')]
        folder_names = [f.strip() for f in folder_rules.split(';')]

        # Input validation
        if len(keyword_groups) != len(folder_names):
            raise ValueError(
                f"Rule mismatch: Found {len(keyword_groups)} keyword groups "
                f"but {len(folder_names)} folders. They must match."
            )
        
        if not keyword_rules or not folder_rules:
             raise ValueError("Keyword and folder rules cannot be empty.")

        for i, group in enumerate(keyword_groups):
            folder = folder_names[i]
            keys = [k.strip().lower() for k in group.split(',') if k.strip()]
            for key in keys:
                if key in keyword_map:
                    self.logger.warning(f"Keyword '{key}' is defined in multiple groups. "
                                      f"Using the last one ('{folder}').")
                keyword_map[key] = folder
        
        return keyword_map

    def organize_files(self, sort_mode, granularity, keyword_rules, folder_rules, create_parent_folders):
        """
        Classifies and moves all files in the source directory based
        on the selected single organization mode.

        Args:
            sort_mode (str): "By Extension", "By Time", or "By Keyword".
            granularity (str): Time granularity ("Year", "Month", etc.).
            keyword_rules (str): User-defined keyword rule string.
            folder_rules (str): User-defined folder rule string.
            create_parent_folders (bool): For Time sort, whether to create
                                          nested folders (e.g., YYYY/MM/DD).
        """
        self.logger.info(f"Starting organization with mode: {sort_mode}")
        current_batch_history = []
        
        # --- 1. Prepare Rules ---
        keyword_map = {}
        if sort_mode == "By Keyword":
            keyword_map = self._build_keyword_map(keyword_rules, folder_rules)
            if not keyword_map:
                raise ValueError("No valid keyword rules were provided.")
        
        # --- 2. Collect Files ---
        files_to_process = []
        for item in self.source_directory.iterdir():
            # Ignore self-generated files and directories
            if item.is_file() and item.name not in ['.organizer_history.json', '.organizer_config.json']:
                files_to_process.append(item)
                
        total_files = len(files_to_process)
        if total_files == 0:
            self.logger.info("No files to organize.")
            return 0
        
        self.logger.info(f"Found {total_files} files to process.")

        # --- 3. Process Files ---
        for index, item in enumerate(files_to_process):
            
            # --- Mode 1: By Keyword ---
            if sort_mode == "By Keyword":
                file_moved = False
                for key, folder_name in keyword_map.items():
                    if key in item.name.lower():
                        dest_folder = self.source_directory / folder_name
                        self._move_file(item, dest_folder, current_batch_history)
                        file_moved = True
                        break # Keyword found, move to next file
                
            # --- Mode 2: By Time ---
            elif sort_mode == "By Time":
                try:
                    m_time = datetime.fromtimestamp(item.stat().st_mtime)
                    time_folder_path = None # Use Path object

                    if create_parent_folders:
                        # Build a nested path: e.g., 2025/11/10/16h/30m
                        parts = []
                        if granularity == "Decade":
                            parts.append(f"{m_time.year // 10 * 10}s")
                        else: # All other granularities start with Year
                            parts.append(f"{m_time.year}")
                        
                        if granularity in ["Month", "Day", "Hour", "Minute", "Second"]:
                            parts.append(f"{m_time.month:02d}")
                        if granularity in ["Day", "Hour", "Minute", "Second"]:
                            parts.append(f"{m_time.day:02d}")
                        if granularity in ["Hour", "Minute", "Second"]:
                            parts.append(f"{m_time.hour:02d}h")
                        if granularity in ["Minute", "Second"]:
                            parts.append(f"{m_time.minute:02d}m")
                        if granularity == "Second":
                            parts.append(f"{m_time.second:02d}s")
                        
                        time_folder_path = Path(*parts) # Unpack list into Path
                    
                    else:
                        # Build a flat path: e.g., 2025-11-10_16h30m
                        base_name = f"{m_time.year}-{m_time.month:02d}-{m_time.day:02d}"
                        flat_name = ""
                        if granularity == "Decade":
                            flat_name = f"{m_time.year // 10 * 10}s"
                        elif granularity == "Year":
                            flat_name = f"{m_time.year}"
                        elif granularity == "Month":
                            flat_name = f"{m_time.year}-{m_time.month:02d}"
                        elif granularity == "Day":
                            flat_name = base_name
                        elif granularity == "Hour":
                            flat_name = f"{base_name}_{m_time.hour:02d}h"
                        elif granularity == "Minute":
                            flat_name = f"{base_name}_{m_time.hour:02d}h{m_time.minute:02d}m"
                        elif granularity == "Second":
                            flat_name = f"{base_name}_{m_time.hour:02d}h{m_time.minute:02d}m{m_time.second:02d}s"
                        
                        if flat_name:
                            time_folder_path = Path(flat_name)

                    if time_folder_path:
                        dest_folder = self.source_directory / time_folder_path
                        self._move_file(item, dest_folder, current_batch_history)
                except Exception as e:
                    self.logger.error(f"Error processing time for {item.name}: {e}")
            
            # --- Mode 3: By Extension (Default) ---
            elif sort_mode == "By Extension":
                extension = item.suffix
                ext_folder_name = extension[1:].lower() if extension else "other"
                dest_folder = self.source_directory / ext_folder_name
                self._move_file(item, dest_folder, current_batch_history)

            # Update GUI progress bar
            self._report_progress(index + 1, total_files)

        # --- 4. Save History ---
        if current_batch_history:
            try:
                history_stack = self._load_history_stack()
                history_stack.append(current_batch_history)
                with open(self.history_file, 'w') as f:
                    json.dump(history_stack, f, indent=4)
                
                self._set_file_hidden(self.history_file)
                self.logger.info("Successfully saved new batch to history.")
            except Exception as e:
                self.logger.error(f"Error saving history: {e}")
        
        self.logger.info(f"Organization complete. Moved {len(current_batch_history)} files.")
        return len(current_batch_history)

    def undo_organization(self):
        """
        Reverts the last organization batch based on the history file.
        Recursively cleans up any empty folders created by the batch.
        
        Returns:
            int: The number of files successfully restored.
        """
        self.logger.info("Attempting to undo last organization...")
        history_stack = self._load_history_stack()
        
        if not history_stack:
            self.logger.warning("History is empty. Nothing to undo.")
            return 0
            
        last_batch = history_stack.pop()
        files_undone = 0
        folders_to_check = set()
        
        total_files = len(last_batch)
        if total_files == 0:
            return 0
            
        # --- 1. Restore Files ---
        for index, (source_str, dest_str) in enumerate(reversed(last_batch)):
            try:
                shutil.move(dest_str, source_str)
                self.logger.info(f"UNDO: Moved {dest_str} -> {source_str}")
                folders_to_check.add(Path(dest_str).parent) 
                files_undone += 1
            except FileNotFoundError:
                self.logger.warning(f"UNDO: File {dest_str} not found. Already moved?")
            except Exception as e:
                self.logger.error(f"Error undoing move for {dest_str}: {e}")
            self._report_progress(index + 1, total_files)

        # --- 2. Recursive Folder Cleanup ---
        print("\nCleaning up empty folders...")
        sorted_folders = sorted(list(folders_to_check), key=lambda p: len(p.parts), reverse=True)
        
        for folder in sorted_folders:
            current_folder = folder
            while current_folder != self.source_directory and current_folder.exists():
                try:
                    if not any(current_folder.iterdir()):
                        os.rmdir(current_folder)
                        print(f"Removed empty folder: {current_folder.name}")
                        self.logger.info(f"Removed empty folder: {current_folder}")
                    else:
                        break 
                except Exception as e:
                    print(f"Could not remove folder {current_folder.name}: {e}")
                    self.logger.warning(f"Could not remove folder {current_folder}: {e}")
                    break
                
                current_folder = current_folder.parent 

        # --- 3. Save Updated History Stack ---
        try:
            if not history_stack:
                os.remove(self.history_file)
                self.logger.info("Successfully removed empty file.")
            else:
                with open(self.history_file, 'w') as f:
                    json.dump(history_stack, f, indent=4)
                
                self._set_file_hidden(self.history_file)
                self.logger.info("Successfully updated history file.")
        except Exception as e:
            self.logger.error(f"Error updating/removing history file: {e}")
            
        return files_undone

# =================================================================
#  Graphical User Interface
# =================================================================

class FileOrganizerGUI:
    """
    Manages the Tkinter GUI, application state, and user interaction.
    Delegates core logic to the FileOrganizer class.
    """
    def __init__(self, root):
        self.root = root
        self.root.title("Smart File Organizer")
        self.root.geometry("500x530") 
        self.root.resizable(False, False)
        
        # App config is stored next to the script/executable
        self.config_file = get_writable_path() / '.organizer_config.json'
        
        self._load_styles()
        self._init_variables()
        self._create_widgets()
        self._on_sort_mode_changed() # Set initial UI state

    def _load_styles(self):
        """Loads available themes and applies the user's saved theme."""
        self.style = ttk.Style()
        self.available_themes = sorted(list(self.root.get_themes())) 
        config = self._load_config()
        saved_theme = config.get('theme')
        
        if saved_theme and saved_theme in self.available_themes:
            self.root.set_theme(saved_theme)
        elif 'ubuntu' in self.available_themes:
            self.root.set_theme("ubuntu") # Default fallback
        elif self.available_themes:
            self.root.set_theme(self.available_themes[0]) # Any theme
        
    def _init_variables(self):
        """Initializes all Tkinter string/control variables."""
        self.selected_folder = tk.StringVar()
        self.status_message = tk.StringVar()
        self.theme_var = tk.StringVar()
        self.sort_mode = tk.StringVar()
        self.time_granularity = tk.StringVar()
        self.match_keywords = tk.StringVar()
        self.match_folders = tk.StringVar()
        self.progress_var = tk.DoubleVar()
        self.active_thread = None 
        self.time_create_parents = tk.BooleanVar(value=False)

    def _create_widgets(self):
        """Lays out all widgets in the main window."""
        
        # --- Status Bar (Bottom) ---
        status_bar = ttk.Label(self.root, textvariable=self.status_message, relief=tk.SUNKEN, padding=5, anchor=tk.W)
        status_bar.pack(side=tk.BOTTOM, fill=tk.X)
        self.status_message.set("Ready.")

        # --- Theme Selection (Bottom) ---
        theme_frame = ttk.Frame(self.root, padding="5")
        theme_frame.pack(side=tk.BOTTOM, fill=tk.X)
        theme_frame.columnconfigure(0, weight=1)
        theme_frame.columnconfigure(1, weight=2) 
        theme_frame.columnconfigure(2, weight=1)
        
        theme_content_frame = ttk.Frame(theme_frame)
        theme_content_frame.grid(row=0, column=1) 
        theme_label = ttk.Label(theme_content_frame, text="Select Theme:")
        theme_label.pack(side=tk.LEFT, padx=5)
        
        self.theme_combo = ttk.Combobox(theme_content_frame, 
                                   textvariable=self.theme_var, 
                                   values=self.available_themes, 
                                   state='readonly', width=15)
        self.theme_combo.pack(side=tk.LEFT, padx=5)
        self.theme_var.set(self.style.theme_use())
        self.theme_combo.bind('<<ComboboxSelected>>', self.change_theme)

        # --- Main Content Frame (Top) ---
        main_frame = ttk.Frame(self.root, padding="10")
        main_frame.pack(fill=tk.BOTH, expand=True)

        # --- Folder Selection ---
        folder_frame = ttk.Frame(main_frame)
        folder_frame.pack(fill=tk.X, pady=5)
        self.select_button = ttk.Button(folder_frame, text="Select Folder", command=self.select_folder)
        self.select_button.pack(side=tk.LEFT, padx=(0, 10))
        folder_label = ttk.Label(folder_frame, textvariable=self.selected_folder, relief="sunken", padding=5)
        folder_label.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.selected_folder.set("No folder selected...")
        
        # --- Sorting Rules ---
        rules_frame = ttk.LabelFrame(main_frame, text="Sorting Mode", padding=10)
        rules_frame.pack(fill=tk.X, pady=(10,5))
        
        sort_mode_frame = ttk.Frame(rules_frame)
        sort_mode_frame.pack(fill=tk.X)
        sort_label = ttk.Label(sort_mode_frame, text="Organize by:")
        sort_label.pack(side=tk.LEFT, padx=5)
        self.sort_mode_combo = ttk.Combobox(sort_mode_frame, 
                                       textvariable=self.sort_mode,
                                       values=["By Extension", "By Time", "By Keyword"],
                                       state='readonly')
        self.sort_mode_combo.pack(fill=tk.X, expand=True, padx=5)
        self.sort_mode.set("By Extension")
        self.sort_mode_combo.bind('<<ComboboxSelected>>', self._on_sort_mode_changed)

        # --- Dynamic Options Container ---
        self.options_container = ttk.Frame(rules_frame)
        self.options_container.pack(fill=tk.BOTH, expand=True, pady=5)
        
        # --- Options Frame 1: Time ---
        self.time_options_frame = ttk.Frame(self.options_container, padding=(10, 5))
        
        # Frame for line 1: Granularity
        time_combo_frame = ttk.Frame(self.time_options_frame)
        time_label = ttk.Label(time_combo_frame, text="Granularity:")
        time_label.pack(side=tk.LEFT, padx=5)
        self.time_combo = ttk.Combobox(time_combo_frame, 
                                  textvariable=self.time_granularity, 
                                  values=["Decade", "Year", "Month", "Day", "Hour", "Minute", "Second"],
                                  state='readonly')
        self.time_combo.pack(fill=tk.X, expand=True, padx=5)
        time_combo_frame.pack(fill=tk.X)
        self.time_granularity.set("Year")
        
        # Frame for line 2: Parent Folder Checkbox
        time_check_frame = ttk.Frame(self.time_options_frame)
        self.time_create_parents_check = ttk.Checkbutton(
            time_check_frame,
            text="Create all parent folders (e.g., 2025/11/10)",
            variable=self.time_create_parents
        )
        self.time_create_parents_check.pack(side=tk.LEFT, padx=15, pady=5)
        time_check_frame.pack(fill=tk.X)
        
        # --- Options Frame 2: Keyword ---
        self.keyword_options_frame = ttk.Frame(self.options_container, padding=(10, 5))
        kw_label = ttk.Label(self.keyword_options_frame, text="Keyword Groups (e.g., key1,key2; key3):")
        kw_label.pack(anchor=tk.W)
        self.kw_entry = ttk.Entry(self.keyword_options_frame, textvariable=self.match_keywords)
        self.kw_entry.pack(fill=tk.X, expand=True)
        folder_label = ttk.Label(self.keyword_options_frame, text="Destination Folders (e.g., folder1; folder2):")
        folder_label.pack(anchor=tk.W, pady=(5,0))
        self.folder_entry = ttk.Entry(self.keyword_options_frame, textvariable=self.match_folders)
        self.folder_entry.pack(fill=tk.X, expand=True)
        
        # --- Options Frame 3: Extension (Empty for layout consistency) ---
        self.extension_options_frame = ttk.Frame(self.options_container)
        
        # --- Progress Bar ---
        self.progress_bar = ttk.Progressbar(main_frame, variable=self.progress_var, maximum=100)
        self.progress_bar.pack(fill=tk.X, pady=(5,0))

        # --- Action Buttons ---
        button_frame = ttk.Frame(main_frame)
        button_frame.pack(pady=15)
        self.organize_button = ttk.Button(button_frame, text="Organize", command=self.run_organize)
        self.organize_button.pack(side=tk.LEFT, padx=10)
        self.undo_button = ttk.Button(button_frame, text="Undo", command=self.run_undo)
        self.undo_button.pack(side=tk.LEFT, padx=10)

    # --- GUI Logic Methods ---

    def _on_sort_mode_changed(self, event=None):
        """Shows/hides the option frames based on the main combobox."""
        mode = self.sort_mode.get()
        
        # Hide all frames
        self.time_options_frame.pack_forget()
        self.keyword_options_frame.pack_forget()
        self.extension_options_frame.pack_forget()
        
        # Show the relevant frame
        if mode == "By Time":
            self.time_options_frame.pack(fill=tk.BOTH, expand=True)
        elif mode == "By Keyword":
            self.keyword_options_frame.pack(fill=tk.BOTH, expand=True)
        elif mode == "By Extension":
            self.extension_options_frame.pack(fill=tk.BOTH, expand=True)

    def _toggle_controls(self, enabled):
        """Enables or disables all main controls during a task."""
        
        state = 'normal' if enabled else 'disabled'
        combo_state = 'readonly' if enabled else 'disabled'
        
        self.select_button.config(state=state)
        self.organize_button.config(state=state)
        self.undo_button.config(state=state)
        self.kw_entry.config(state=state)
        self.folder_entry.config(state=state)
        
        self.time_create_parents_check.config(state=state)

        self.theme_combo.config(state=combo_state)
        self.sort_mode_combo.config(state=combo_state)
        self.time_combo.config(state=combo_state)

        if enabled:
            self._on_sort_mode_changed()

    def _update_progress(self, percentage):
        """Thread-safe method to update the progress bar from the engine."""
        self.root.after(0, self.progress_var.set, percentage)

    def run_organize(self):
        """
        Validates input and starts the organization process in a 
        separate thread.
        """
        folder = self.selected_folder.get()
        if not os.path.isdir(folder):
            messagebox.showerror("Error", "Please select a valid folder first.")
            return
            
        # Get all rule data from the GUI
        sort_mode = self.sort_mode.get()
        granularity = self.time_granularity.get()
        keyword_rules = self.match_keywords.get()
        folder_rules = self.match_folders.get()
        create_parents = self.time_create_parents.get()
        
        # Disable UI and start background task
        self._toggle_controls(enabled=False)
        self.status_message.set("Organizing... Please wait.")
        self.progress_var.set(0)
        
        self.active_thread = threading.Thread(
            target=self._organize_thread, 
            args=(folder, sort_mode, granularity, keyword_rules, folder_rules, create_parents)
        )
        self.active_thread.start()
        
        # Start polling for thread completion
        self.root.after(100, self._check_thread)

    def run_undo(self):
        """
        Validates input and starts the undo process in a 
        separate thread.
        """
        folder = self.selected_folder.get()
        if not os.path.isdir(folder):
            messagebox.showerror("Error", "Please select a valid folder first.")
            return

        self._toggle_controls(enabled=False)
        self.status_message.set("Undoing... Please wait.")
        self.progress_var.set(0)

        self.active_thread = threading.Thread(
            target=self._undo_thread, 
            args=(folder,)
        )
        self.active_thread.start()
        self.root.after(100, self._check_thread)

    # --- Threading Methods ---

    def _organize_thread(self, folder, sort_mode, granularity, keyword_rules, folder_rules, create_parents):
        """
        Worker function that runs `FileOrganizer.organize_files`
        in the background.
        """
        self.thread_result = None
        try:
            organizer = FileOrganizer(folder, self._update_progress)
            files_moved = organizer.organize_files(
                sort_mode, granularity, keyword_rules, folder_rules, create_parents
            )
            self.thread_result = ("Success", f"Organization complete!\nMoved {files_moved} files.")
        
        # Catch specific validation errors from the engine
        except ValueError as e:
            self.thread_result = ("Error", f"Invalid Rules:\n{e}")
        except Exception as e:
            self.thread_result = ("Error", f"An error occurred:\n{e}")

    def _undo_thread(self, folder):
        """
        Worker function that runs `FileOrganizer.undo_organization`
        in the background.
        """
        self.thread_result = None
        try:
            organizer = FileOrganizer(folder, self._update_progress)
            files_undone = organizer.undo_organization()
            if files_undone == 0:
                self.thread_result = ("Info", "History is empty. Nothing to undo.")
            else:
                self.thread_result = ("Success", f"Undo complete!\nRestored {files_undone} files.")
        except Exception as e:
            self.thread_result = ("Error", f"An error occurred:\n{e}")

    def _check_thread(self):
        """
        Polls the active worker thread. If the thread is finished,
        it re-enables the GUI and shows the result.
        """
        if self.active_thread and self.active_thread.is_alive():
            # Thread is still running, check again
            self.root.after(100, self._check_thread)
        else:
            # Thread is done, re-enable GUI
            self._toggle_controls(enabled=True) 
            self.progress_var.set(0)
            
            # Show result message
            if self.thread_result:
                msg_type, message = self.thread_result
                self.status_message.set(message.split('\n')[0])
                
                if msg_type == "Success":
                    messagebox.showinfo("Success", message)
                elif msg_type == "Info":
                    messagebox.showinfo("Undo", message)
                elif msg_type == "Error":
                    messagebox.showerror("Error", message)
            
            self.active_thread = None
            self.thread_result = None

    # --- Config and Theme Methods ---

    def _load_config(self):
        """Loads the app config JSON file. Returns empty dict on failure."""
        try:
            if self.config_file.exists():
                with open(self.config_file, 'r') as f:
                    return json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            print(f"Error loading config: {e}")
        return {} 

    def _set_file_hidden(self, file_path):
        """Sets the 'hidden' attribute for a file on Windows."""
        if os.name == 'nt':
            try:
                import ctypes
                ret = ctypes.windll.kernel32.SetFileAttributesW(str(file_path), 2)
                if not ret:
                    print(f"Warning: Failed to set hidden attribute on {file_path}")
            except Exception as e:
                print(f"Warning: Could not set hidden attribute on {file_path}: {e}")

    def _save_config(self, config_data):
        """Saves the given dict to the app config JSON file."""
        try:
            with open(self.config_file, 'w') as f:
                json.dump(config_data, f, indent=4)
            
            self._set_file_hidden(self.config_file)
        except IOError as e:
            print(f"Error saving config: {e}")

    def change_theme(self, event):
        """Applies the selected theme and saves the choice to config."""
        selected_theme = self.theme_var.get()
        try:
            self.root.set_theme(selected_theme)
            self.status_message.set(f"Theme changed to: {selected_theme}")
            self._save_config({'theme': selected_theme})
        except tk.TclError:
            self.status_message.set(f"Failed to change theme to: {selected_theme}")

    def select_folder(self):
        """Opens the system's folder selection dialog."""
        folder_path = filedialog.askdirectory()
        if folder_path:
            self.selected_folder.set(folder_path)
            self.status_message.set(f"Selected: {folder_path}")

# =================================================================
#  Application Entry Point
# =================================================================

if __name__ == "__main__":
    # ThemedTk must be the root window for themes to apply
    root = ThemedTk()
    app = FileOrganizerGUI(root)
    root.mainloop()
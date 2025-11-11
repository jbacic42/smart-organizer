# Smart File Organizer

A cross-platform desktop utility for sorting files based on user-defined rules.

The application is built in Python using `tkinter` for the GUI and `ttkthemes` for styling. All file operations are non-blocking, running in a separate thread to keep the UI responsive.

---

## Features

* **Rule-Based Sorting:** Organize files using one of three mutually exclusive modes:
    * **By Extension:** Sorts files into folders named after their file extension (e.g., `.pdf`, `.jpg`).
    * **By Time:** Sorts files into folders based on modification timestamp. Granularity is user-selectable from "Decade" down to "Second".
    * **By Keyword:** Sorts files into user-defined folders based on keyword matches within the filename.
* **Keyword Rule Engine:** Parses a simple syntax for mapping keywords to folders.
    * **Keyword Groups:** `key1,key2; key3`
    * **Destination Folders:** `Folder1; Folder2`
    * The number of semicolon-separated keyword groups must match the number of destination folders.
* **Nested Time Sorting:** An optional mode for time-based sorting that creates a nested directory structure (e.g., `YYYY/MM/DD`) instead of a single flat folder name.
* **Multi-Level Undo:** Reverts the last organization batch by reading a JSON-based history file (`.organizer_history.json`). This operation includes recursive cleanup of any empty directories created by the undone batch.
* **Persistent Configuration:** The selected theme is saved to `.organizer_config.json` and loaded on application start.
* **Logging:** All file operations are logged to `organizer.log` for debugging and review.

---

## Operation

1.  **Select Directory:** Use the "Select Folder" button to choose the target directory.
2.  **Select Sort Mode:** Use the "Organize by:" dropdown to select the sorting method.
3.  **Configure Rules:**
    * **By Time:** Select the desired time granularity. Optionally, check "Create all parent folders" to enable nested directory creation.
    * **By Keyword:** Populate the "Keyword Groups" and "Destination Folders" text fields, using a semicolon (`;`) to separate groups and a comma (`,`) to separate individual keywords within a group.
4.  **Execute:** Click "Organize" to begin the file operation. The UI will remain responsive and show progress.
5.  **Revert:** Click "Undo" to revert the most recent operation batch.

---

## Running the Application

### Pre-compiled Executable

1.  Navigate to the [**Releases**](https://github.com/jbacic42/smart-organizer/releases) page.
2.  Download the latest executable for your platform (`SmartFileOrganizer.exe` for Windows or `SmartFileOrganizer` for Linux).
3.  On Linux, grant execute permissions: `chmod +x SmartFileOrganizer`.
4.  Run the file.

### Building from Source

1.  **Clone the repository:**
    ```bash
    git clone [https://github.com/your-username/smart-organizer.git](https://github.com/your-username/smart-organizer.git)
    cd smart-organizer
    ```

2.  **Install `tkinter` (if missing):**
    This component is often omitted in minimal Linux installations and cannot be installed via `pip`.
    * **Debian/Ubuntu:** `sudo apt-get install python3-tk`
    * **Arch Linux:** `sudo pacman -S tk`
    * **Fedora:** `sudo dnf install python3-tk`

3.  **Create and activate a virtual environment:**
    ```bash
    python3 -m venv venv
    source venv/bin/activate
    ```

4.  **Install dependencies:**
    ```bash
    pip install -r requirements.txt
    ```

5.  **Run the application:**
    ```bash
    python organizer.py
    ```
## Requirements

The runtime dependencies for this project are:

pillow==12.0.0 ttkthemes==3.3.0
---


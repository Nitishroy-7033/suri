"""Sight: the webcam, motion detection, and describing what it sees.

    camera.py    one shared webcam, opened only while someone needs it
    motion.py    background-subtraction detector + watcher that raises events
    describe.py  image -> words via a vision model
    tools.py     look, start/stop_motion_watch, camera_status
"""

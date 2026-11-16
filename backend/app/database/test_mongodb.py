from mongodb import test_connection

if test_connection():
    print("MongoDB connection SUCCESSFUL!")
else:
    print("MongoDB connection FAILED!")
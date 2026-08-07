from cryptography.fernet import Fernet
import secrets
import string

def generate_secret_key():
    alphabet = string.ascii_letters + string.digits + "!@#$%^&*()"
    return ''.join(secrets.choice(alphabet) for _ in range(50))

print(f"SECRET_KEY={generate_secret_key()}")
print(f"ENCRYPTION_KEY={Fernet.generate_key().decode()}")

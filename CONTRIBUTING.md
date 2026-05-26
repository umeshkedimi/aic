# Contributing to AIC

Thank you for your interest in contributing to AIC (AI Incident Commander)!

## Development Setup

### Prerequisites

- Python 3.12+
- Docker and Docker Compose
- Git

### Local Setup

```bash
# Clone the repository
git clone https://github.com/YOUR_USERNAME/aic.git
cd aic

# Create environment file
cp .env.example .env
# Edit .env and add your OPENAI_API_KEY

# Start infrastructure
make docker-up

# Install dependencies
make install

# Run migrations
make migrate

# Start development server
make dev
```

### Running Tests

```bash
# Run all tests
make test

# Run with coverage
make test-cov

# Run only unit tests
make test-unit
```

### Code Quality

Before submitting a PR, ensure your code passes all checks:

```bash
# Run linting
make lint

# Run type checking
make typecheck

# Run all checks
make check
```

## Pull Request Process

1. **Fork** the repository
2. **Create a branch** for your feature (`git checkout -b feature/amazing-feature`)
3. **Make your changes** following our coding standards
4. **Write tests** for new functionality
5. **Run checks** (`make check`)
6. **Commit** with clear messages following [Conventional Commits](https://www.conventionalcommits.org/)
7. **Push** to your fork
8. **Open a Pull Request**

## Coding Standards

- **Type hints**: Required on all function signatures
- **Async**: Use `async def` for all I/O operations
- **Formatting**: We use `black` and `ruff`
- **Documentation**: Docstrings for public APIs (Google style)

## Commit Messages

Follow [Conventional Commits](https://www.conventionalcommits.org/):

```
feat: add incident correlation engine
fix: resolve race condition in analysis service
docs: update API documentation
refactor: simplify RCA pipeline
test: add integration tests for RAG retrieval
chore: update dependencies
```

## Security

- **Never commit secrets** — use environment variables
- Report security vulnerabilities privately (see SECURITY.md)

## Questions?

Open an issue for questions or discussion.

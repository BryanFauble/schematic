import pytest

# Fixture for setup and cleanup limited to the MyTestClass.test_example method
# @pytest.fixture(scope="class", autouse=True)
# def setup_and_cleanup(request):
#     # Setup code (run before the first invocation of test_example)
#     print('cleaning up')

# Provide cleanup code (this gets executed after the last invocation of test_example)


def test_example():
    # Your test code here
    print("Running test with param=")


@pytest.fixture(scope="function")
def setup_and_cleanup(request):
    if request.function._test_has_run_called:
        return
    print("running set up")


# Test class
class TestClass:
    # Parametrized test method
    @pytest.mark.parametrize("param", [1, 2, 3])
    def test_example(self, param, setup_and_cleanup):
        # Your test code here
        print(f"Running test with param={param}")

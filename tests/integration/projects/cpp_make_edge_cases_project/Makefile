CXX = g++
CXXFLAGS = -Iinclude -std=c++17

all: demo

demo: src/main.o src/greeter.o
	$(CXX) $(CXXFLAGS) -o demo src/main.o src/greeter.o

src/main.o: src/main.cpp include/greeter.hpp
	$(CXX) $(CXXFLAGS) -c src/main.cpp -o src/main.o

src/greeter.o: src/greeter.cpp include/greeter.hpp
	$(CXX) $(CXXFLAGS) -c src/greeter.cpp -o src/greeter.o

clean:
	rm -f src/*.o demo

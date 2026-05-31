CXX = g++
CXXFLAGS = -Iinclude -std=c++17

OBJS = src/main.o src/models/base.o src/models/entities.o src/services/processor.o

all: demo

demo: $(OBJS)
	$(CXX) $(CXXFLAGS) -o demo $(OBJS)

src/main.o: src/main.cpp include/models/entities.hpp include/services/processor.hpp
	$(CXX) $(CXXFLAGS) -c src/main.cpp -o src/main.o

src/models/base.o: src/models/base.cpp include/models/base.hpp
	$(CXX) $(CXXFLAGS) -c src/models/base.cpp -o src/models/base.o

src/models/entities.o: src/models/entities.cpp include/models/entities.hpp include/models/base.hpp
	$(CXX) $(CXXFLAGS) -c src/models/entities.cpp -o src/models/entities.o

src/services/processor.o: src/services/processor.cpp include/services/processor.hpp include/models/base.hpp
	$(CXX) $(CXXFLAGS) -c src/services/processor.cpp -o src/services/processor.o

clean:
	rm -f src/*.o src/models/*.o src/services/*.o demo

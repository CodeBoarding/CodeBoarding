CXX = g++
CXXFLAGS = -I../include -std=c++17

OBJS = main.o models/base.o models/entities.o services/processor.o

all: demo

demo: $(OBJS)
	$(CXX) $(CXXFLAGS) -o demo $(OBJS)

main.o: main.cpp ../include/models/entities.hpp ../include/services/processor.hpp
	$(CXX) $(CXXFLAGS) -c main.cpp -o main.o

models/base.o: models/base.cpp ../include/models/base.hpp
	$(CXX) $(CXXFLAGS) -c models/base.cpp -o models/base.o

models/entities.o: models/entities.cpp ../include/models/entities.hpp ../include/models/base.hpp
	$(CXX) $(CXXFLAGS) -c models/entities.cpp -o models/entities.o

services/processor.o: services/processor.cpp ../include/services/processor.hpp ../include/models/base.hpp
	$(CXX) $(CXXFLAGS) -c services/processor.cpp -o services/processor.o

clean:
	rm -f *.o models/*.o services/*.o demo

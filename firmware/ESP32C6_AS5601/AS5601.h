#ifndef AS5601_h
#define AS5601_h

#include <Arduino.h>
#include <Wire.h>

class AS5601
{
public:
	explicit AS5601(TwoWire &wire = Wire, uint8_t address = 0x36, bool debug = false);

	void begin(uint32_t clockHz = 400000, int sdaPin = -1, int sclPin = -1);

	uint16_t getRawAngle();
	double getAngleDegrees();
	double getAngleRadians();

	void setZeroPosition(uint16_t zeroPosition);
	uint16_t getZeroPosition() const;

	uint8_t getStatus();
	uint8_t getAutomaticGainControl();
	uint16_t getMagnitude();

	String getDiagnostic();

private:
	TwoWire *wire;
	uint8_t address;
	bool debug;
	uint16_t zeroPosition;

	static constexpr uint16_t MAX_STEPS = 4096;

	bool readRegisters(uint8_t reg, uint8_t *buffer, size_t length);
	bool writeRegister(uint8_t reg, uint8_t value);
	uint16_t normalize(uint16_t raw) const;
};

#endif

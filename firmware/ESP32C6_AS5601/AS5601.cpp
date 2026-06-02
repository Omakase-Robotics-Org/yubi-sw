#include "AS5601.h"

static constexpr uint8_t AS5601_REG_STATUS = 0x0B;
static constexpr uint8_t AS5601_REG_RAW_ANGLE = 0x0C;
static constexpr uint8_t AS5601_REG_AGC = 0x1A;
static constexpr uint8_t AS5601_REG_MAGNITUDE_H = 0x1B;
static constexpr uint8_t AS5601_REG_MAGNITUDE_L = 0x1C;

static constexpr uint8_t AS5601_STATUS_MD = 0x20;
static constexpr uint8_t AS5601_STATUS_ML = 0x10;
static constexpr uint8_t AS5601_STATUS_MH = 0x08;

static constexpr uint16_t AS5601_VALUE_MASK = 0x0FFF;

AS5601::AS5601(TwoWire &wireRef, uint8_t i2cAddress, bool enableDebug)
	: wire(&wireRef), address(i2cAddress), debug(enableDebug), zeroPosition(0)
{
}

void AS5601::begin(uint32_t clockHz, int sdaPin, int sclPin)
{
#if defined(ESP32) || defined(ARDUINO_ARCH_ESP32)
	if (sdaPin >= 0 && sclPin >= 0)
	{
		this->wire->begin(sdaPin, sclPin);
	}
	else
	{
		this->wire->begin();
	}
#else
	(void)sdaPin;
	(void)sclPin;
	this->wire->begin();
#endif

	this->wire->setClock(clockHz);
}

uint16_t AS5601::getRawAngle()
{
	uint8_t buffer[2] = {0};
	if (!readRegisters(AS5601_REG_RAW_ANGLE, buffer, sizeof(buffer)))
	{
		return 0;
	}

	uint16_t raw = static_cast<uint16_t>(((static_cast<uint16_t>(buffer[0]) << 8) | buffer[1]) & AS5601_VALUE_MASK);
	return raw;
}

double AS5601::getAngleDegrees()
{
	uint16_t normalized = normalize(getRawAngle());
	return (static_cast<double>(normalized) * 360.0) / static_cast<double>(MAX_STEPS);
}

double AS5601::getAngleRadians()
{
	uint16_t normalized = normalize(getRawAngle());
	return (static_cast<double>(normalized) * (2.0 * PI)) / static_cast<double>(MAX_STEPS);
}

void AS5601::setZeroPosition(uint16_t zero)
{
	this->zeroPosition = static_cast<uint16_t>(zero % MAX_STEPS);
}

uint16_t AS5601::getZeroPosition() const
{
	return this->zeroPosition;
}


uint8_t AS5601::getStatus()
{
	uint8_t status = 0;
	if (!readRegisters(AS5601_REG_STATUS, &status, 1))
	{
		return 0;
	}
	return status;
}

uint8_t AS5601::getAutomaticGainControl()
{
	uint8_t agc = 0;
	if (!readRegisters(AS5601_REG_AGC, &agc, 1))
	{
		return 0;
	}
	return agc;
}

uint16_t AS5601::getMagnitude()
{
	uint8_t buffer[2] = {0};
	if (!readRegisters(AS5601_REG_MAGNITUDE_H, buffer, sizeof(buffer)))
	{
		return 0;
	}
		uint16_t magnitude = static_cast<uint16_t>((static_cast<uint16_t>(buffer[0]) << 8) | buffer[1]);
		return magnitude & AS5601_VALUE_MASK;
}

String AS5601::getDiagnostic()
{
	uint8_t status = getStatus();
	if ((status & AS5601_STATUS_MD) == 0)
	{
		return "No magnet detected";
	}
	if (status & AS5601_STATUS_ML)
	{
		return "Magnetic field too weak";
	}
	if (status & AS5601_STATUS_MH)
	{
		return "Magnetic field too strong";
	}
	return "Magnetic field OK";
}

bool AS5601::readRegisters(uint8_t reg, uint8_t *buffer, size_t length)
{
	if (buffer == nullptr || length == 0)
	{
		return false;
	}

	this->wire->beginTransmission(this->address);
	this->wire->write(reg);
	uint8_t txStatus = this->wire->endTransmission(false);
	if (txStatus != 0)
	{
		if (this->debug)
		{
			Serial.print("AS5601 I2C request failed: ");
			Serial.println(txStatus);
		}
		return false;
	}

	uint8_t toRead = static_cast<uint8_t>(length);
	uint8_t readCount = this->wire->requestFrom(this->address, toRead);
	if (readCount != toRead)
	{
		if (this->debug)
		{
			Serial.println("AS5601 I2C short read");
		}
		return false;
	}

	for (size_t i = 0; i < length; ++i)
	{
		buffer[i] = static_cast<uint8_t>(this->wire->read());
	}
	return true;
}

bool AS5601::writeRegister(uint8_t reg, uint8_t value)
{
	this->wire->beginTransmission(this->address);
	this->wire->write(reg);
	this->wire->write(value);
	uint8_t txStatus = this->wire->endTransmission();
	if (txStatus != 0)
	{
		if (this->debug)
		{
			Serial.print("AS5601 I2C write failed: ");
			Serial.println(txStatus);
		}
		return false;
	}
	return true;
}

uint16_t AS5601::normalize(uint16_t raw) const
{
	uint16_t offset = static_cast<uint16_t>(this->zeroPosition % MAX_STEPS);
	uint16_t adjusted = static_cast<uint16_t>((raw + MAX_STEPS - offset) & AS5601_VALUE_MASK);
	return adjusted;
}
